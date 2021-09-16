#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2021 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import argparse
import concurrent.futures
import dataclasses
import enum
import itertools
import logging
import os
import typing

import ccc.oci
import ci.util
import cnudie.replicate
import cnudie.retrieve
import container.util
import gci.componentmodel as cm
import product.v2
import yaml

import ctt.filters as filters
import ctt.processing_model as processing_model
import ctt.processors as processors
import ctt.uploaders as uploaders
from ctt.rbsc_bom import BOMEntry, BOMEntryType, buildAndApplyBOM

logger = logging.getLogger(__name__)

own_dir = os.path.abspath(os.path.dirname(__file__))

bom_resources: typing.Sequence[BOMEntry] = []


class ProcessingMode(enum.Enum):
    REGULAR = 'regular'
    DRY_RUN = 'dry_run'


class ProcessingPipeline:
    def __init__(
        self,
        name,
        filters,
        processor,
        uploaders,
    ):
        self._name = name
        self._filters = filters
        self._processor = processor
        self._uploaders = uploaders

    def matches(
        self,
        component: cm.Component,
        resource: cm.Resource,
    ):
        filters_count = len(self._filters)
        return all(
            map(
                lambda filtr, component, resource: filtr.matches(component, resource),
                self._filters,
                itertools.repeat(component, filters_count),
                itertools.repeat(resource, filters_count),
            )
        )

    def process(
        self,
        component: cm.Component,
        resource: cm.Resource,
        processing_mode: ProcessingMode,
    ) -> processing_model.ProcessingJob:
        if not self.matches(component, resource):
            return None

        logging.info(
            f'{self._name} will process: '
            f'{component.name}:{resource.type}:{resource.access}'
        )

        job = processing_model.ProcessingJob(
            component=component,
            resource=resource,
            upload_request=processing_model.ContainerImageUploadRequest(
                source_ref=None,
                target_ref=None,  # must be set by a later step
                remove_files=None,  # _may_ be set by a later step
            ),
        )

        job = self._processor.process(processing_job=job)

        first = True
        for uploader in self._uploaders:
            job = uploader.process(job, target_as_source=not first)
            first = False

        lssd_label = create_lssd_label(
            processing_rules=[
                self._name,
            ],
        )
        patched_resource = job.processed_resource.set_label(
            label=lssd_label,
        )
        job = dataclasses.replace(
            job,
            processed_resource=patched_resource,
        )

        return job


def create_lssd_label(
    processing_rules: typing.List[str],
) -> cm.Label:
    lssd_label_name = 'cloud.gardener.cnudie/sdo/lssd'
    label = cm.Label(
        name=lssd_label_name,
        value={
            'processingRules': processing_rules,
        },
    )

    return label


def parse_processing_cfg(path):
    raw_cfg = ci.util.parse_yaml_file(path)

    processing_cfg_dir = os.path.abspath(os.path.dirname(path))
    for name, cfg in raw_cfg.get('processors', {}).items():
        cfg['kwargs']['base_dir'] = processing_cfg_dir

    return raw_cfg


def _filter(filter_cfg: dict):
    filter_ctor = getattr(filters, filter_cfg['type'])
    filter_ = filter_ctor(**filter_cfg.get('kwargs', {}))

    return filter_


def _processor(processor_cfg: dict):
    proc_type = processor_cfg['type']
    proc_ctor = getattr(processors, proc_type, None)
    if not proc_ctor:
        ci.util.fail(f'no such image processor: {proc_type}')
    processor = proc_ctor(**processor_cfg.get('kwargs', {}))
    return processor


def _uploader(uploader_cfg: dict):
    upload_type = uploader_cfg['type']
    upload_ctor = getattr(uploaders, upload_type, None)
    if not upload_ctor:
        ci.util.fail(f'no such uploader: {upload_type}')
    uploader = upload_ctor(**uploader_cfg.get('kwargs', {}))
    return uploader


def processing_pipeline(
    processing_cfg: dict,
    shared_processors: dict = {},
    shared_uploaders: dict = {},
) -> ProcessingPipeline:
    name = processing_cfg.get('name', '<no name>')

    filter_cfgs = processing_cfg['filter']
    if isinstance(filter_cfgs, dict):
        filter_cfgs = [filter_cfgs]
    filters = [_filter(filter_cfg=filter_cfg) for filter_cfg in filter_cfgs]

    if 'processor' in processing_cfg:
        processor_cfg = processing_cfg['processor']
        if isinstance(processor_cfg, str):
            proc = shared_processors[processor_cfg]
        else:
            proc = _processor(processor_cfg=processor_cfg)
    else:
        proc = processors.NoOpProcessor()

    upload_cfgs = processing_cfg['upload']
    if not isinstance(upload_cfgs, list):
        upload_cfgs = [upload_cfgs]  # normalise to list

    def instantiate_uploader(upload_cfg):
        if isinstance(upload_cfg, str):
            return shared_uploaders[upload_cfg]
        return _uploader(upload_cfg)

    uploaders = [instantiate_uploader(upload_cfg) for upload_cfg in upload_cfgs]

    pipeline = ProcessingPipeline(
        name=name,
        filters=filters,
        processor=proc,
        uploaders=uploaders,
    )
    return pipeline


def enum_processing_cfgs(
    processing_cfg: dict,
    shared_processors: dict,
    shared_uploaders: dict,
):
    cfg_entries = processing_cfg['image_processing_cfg']

    yield from map(
        processing_pipeline,
        cfg_entries,
        itertools.repeat(shared_processors, len(cfg_entries)),
        itertools.repeat(shared_uploaders, len(cfg_entries)),
    )


def create_jobs(
    processing_cfg_path,
    component_descriptor_v2: cm.ComponentDescriptor,
    processing_mode,
):
    processing_cfg = parse_processing_cfg(processing_cfg_path)

    shared_processors = {
        name: _processor(cfg) for name, cfg in processing_cfg.get('processors', {}).items()
    }
    shared_uploaders = {
        name: _uploader(cfg) for name, cfg in processing_cfg.get('uploaders', {}).items()
    }

    components = cnudie.retrieve.components(component=component_descriptor_v2)

    def enumerate_component_and_oci_resources():
        for component in components:
            for oci_resource in product.v2.resources(
                component=component,
                resource_access_types=(cm.AccessType.OCI_REGISTRY, cm.AccessType.RELATIVE_OCI_REFERENCE),
                resource_types=None,  # yields all resource types
                resource_policy=product.v2.ResourcePolicy.IGNORE_NONMATCHING_ACCESS_TYPES,
            ):
                yield component, oci_resource

    # XXX only support OCI-resources for now
    for component, oci_resource in enumerate_component_and_oci_resources():
        for pipeline in enum_processing_cfgs(
            parse_processing_cfg(processing_cfg_path),
            shared_processors,
            shared_uploaders,
        ):
            job = pipeline.process(
                component=component,
                resource=oci_resource,
                processing_mode=processing_mode,
            )
            if not job:
                continue  # pipeline did not want to process
            yield job
            break
        else:
            ci.util.warning(
                f' no matching processor: {component.name}:{oci_resource.access}'
            )


# uploads a single OCI artifact and returns the content digest
def process_upload_request(
    upload_request: processing_model.ContainerImageUploadRequest,
    upload_mode_images=product.v2.UploadMode.SKIP
) -> str:
    tgt_ref = upload_request.target_ref

    oci_client = ccc.oci.oci_client()
    manifest_blob_ref = oci_client.head_manifest(image_reference=tgt_ref, absent_ok=True)
    if bool(manifest_blob_ref) and upload_mode_images is product.v2.UploadMode.SKIP:
        logger.info(f'{tgt_ref=} exists - skipping processing')
        return manifest_blob_ref.digest

    src_ref = upload_request.source_ref

    logger.info(f'start processing {src_ref} -> {tgt_ref=}')

    res, _, _ = container.util.filter_image(
        source_ref=src_ref,
        target_ref=tgt_ref,
        remove_files=upload_request.remove_files,
    )

    logger.info(f'finished processing {src_ref} -> {tgt_ref=}')

    docker_content_digest = res.headers.get('Docker-Content-Digest', None)
    return docker_content_digest


def replace_tag_with_digest(image_reference: str, docker_content_digest: str) -> str:
    last_part = image_reference.split('/')[-1]
    if '@' in last_part:
        src_name, _ = image_reference.rsplit('@', 1)
    else:
        src_name, _ = image_reference.rsplit(':', 1)

    return f'{src_name}@{docker_content_digest}'


def access_resource_via_digest(res: cm.Resource, docker_content_digest: str) -> cm.Resource:
    if res.access.type is cm.AccessType.OCI_REGISTRY:
        digest_ref = replace_tag_with_digest(res.access.imageReference, docker_content_digest)
        digest_access = cm.OciAccess(
            cm.AccessType.OCI_REGISTRY,
            imageReference=digest_ref,
        )
    elif res.access.type is cm.AccessType.RELATIVE_OCI_REFERENCE:
        digest_ref = replace_tag_with_digest(res.access.reference, docker_content_digest)
        digest_access = cm.RelativeOciAccess(
            cm.AccessType.RELATIVE_OCI_REFERENCE,
            reference=digest_ref
        )
    else:
        raise NotImplementedError

    return dataclasses.replace(
        res,
        access=digest_access,
    )


def process_images(
    processing_cfg_path,
    component_descriptor_v2,
    tgt_ctx_base_url: str,
    processing_mode=ProcessingMode.REGULAR,
    upload_mode=None,
    upload_mode_cd=product.v2.UploadMode.SKIP,
    upload_mode_images=product.v2.UploadMode.SKIP,
    replace_resource_tags_with_digests=False,
    skip_cd_validation=False,
):
    if processing_mode is ProcessingMode.DRY_RUN:
        ci.util.warning('dry-run: not downloading or uploading any images')
    else:
        logger.info(f'using upload_mode_cd {upload_mode_cd}')
        logger.info(f'using upload_mode_images {upload_mode_images}')
        logger.info(f'using skip_cd_validation {skip_cd_validation}')

    if upload_mode_images is product.v2.UploadMode.FAIL:
        raise NotImplementedError('upload-mode-image=fail is not a valid argument.')

    if upload_mode is not None:
        logger.warning(f'''upload_mode is deprecated for function process_images.
                        Please use upload_mode_cd and upload_mode_images.
                        Setting upload_mode_cd to {upload_mode} and defaulting upload_mode_images to {upload_mode_images}''')
        upload_mode_cd = upload_mode

    src_ctx_base_url = component_descriptor_v2.component.current_repository_ctx().baseUrl

    if src_ctx_base_url == tgt_ctx_base_url:
        raise RuntimeError('current repo context and target repo context must be different!')

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=16)

    jobs = create_jobs(
        processing_cfg_path,
        component_descriptor_v2=component_descriptor_v2,
        processing_mode=processing_mode,
    )

    def process_job(processing_job: processing_model.ProcessingJob):
        # do actual processing
        if processing_mode is ProcessingMode.REGULAR:
            docker_content_digest = process_upload_request(processing_job.upload_request, upload_mode_images)

            if replace_resource_tags_with_digests:
                if not docker_content_digest:
                    raise RuntimeError(f'No Docker_Content_Digest returned for {processing_job=}')

                processing_job.upload_request = dataclasses.replace(
                    processing_job.upload_request,
                    target_ref=replace_tag_with_digest(processing_job.upload_request.target_ref, docker_content_digest),
                )

                if processing_job.processed_resource:
                    processing_job.processed_resource = access_resource_via_digest(processing_job.processed_resource, docker_content_digest)
                else:
                    processing_job.resource = access_resource_via_digest(processing_job.resource, docker_content_digest)

            bom_resources.append(
                BOMEntry(
                    processing_job.upload_request.target_ref,
                    BOMEntryType.Docker,
                    f'{processing_job.component.name}/{processing_job.resource.name}',
                )
            )
        elif processing_mode == ProcessingMode.DRY_RUN:
            pass
        else:
            raise NotImplementedError(processing_mode)

        return processing_job

    jobs = executor.map(process_job, jobs)

    # group jobs by component-version (TODO: either make Component immutable, or implement
    # __eq__ / __hash__
    def cname_version(component):
        return (component.name, component.version)

    def job_cname_version(job: processing_model.ProcessingJob):
        return cname_version(job.component)

    def append_ctx_repo(ctx_base_url, component):
        if component.current_repository_ctx().baseUrl != ctx_base_url:
            component.repositoryContexts.append(
                cm.OciRepositoryContext(
                    baseUrl=ctx_base_url,
                    type=cm.AccessType.OCI_REGISTRY,
                ),
            )

    components = []
    for _, job_group in itertools.groupby(
        sorted(jobs, key=job_cname_version),
        job_cname_version,
    ):

        patched_resources = {}

        # patch-in overwrites (caveat: must be done sequentially, as lists are not threadsafe)
        for job in job_group:
            component = job.component
            patched_resource = job.processed_resource or job.resource
            patched_resources[job.resource.identity(component.resources)] = patched_resource
            continue

        res_list = []
        for res in component.resources:
            if res.identity(component.resources) in patched_resources:
                res_list.append(patched_resources[res.identity(component.resources)])
            else:
                res_list.append(res)

        components.append(dataclasses.replace(
            component,
            resources=res_list,
        ))

    processed_component_versions = {cname_version(c) for c in components}

    # hack: add all components w/o resources (those would otherwise be ignored)
    for component in product.v2.components(component_descriptor_v2=component_descriptor_v2):
        if not cname_version(component) in processed_component_versions:
            components.append(component)
            processed_component_versions.add(cname_version(component))

    for component in components:
        append_ctx_repo(src_ctx_base_url, component)
        append_ctx_repo(tgt_ctx_base_url, component)
        bom_resources.append(
            BOMEntry(
                product.v2._target_oci_ref(component),
                BOMEntryType.Docker,
                component.name,
            )
        )

    source_comp = component_descriptor_v2.component

    # publish the (patched) component-descriptors
    def reupload_component(component: cm.Component):
        component_descriptor = dataclasses.replace(
            component_descriptor_v2,
            component=component,
        )

        # Validate the patched component-descriptor and exit on fail
        if not skip_cd_validation:
            try:
                dict_cd = yaml.safe_load(yaml.dump(data=dataclasses.asdict(component_descriptor), Dumper=cm.EnumValueYamlDumper))
                cm.ComponentDescriptor.validate(dict_cd, validation_mode=cm.ValidationMode.FAIL)
            except Exception as e:
                logger.error(f'Schema validation for component-descriptor '
                             f'{component_descriptor.component.name}:{component_descriptor.component.version} failed with {e}')
                raise e

        src_ctx_repo_base_url = component_descriptor.component.repositoryContexts[-2].baseUrl
        if processing_mode is ProcessingMode.REGULAR:
            if component.name == source_comp.name and component.version == source_comp.version:
                # we must differentiate whether the input component descriptor (1) exists in the
                # source context or (2) not (e.g. if a component descriptor from a local file is used).
                # for case (2) the copying of resources isn't supported by the coding.
                cd_exists_in_src_ctx = product.v2.download_component_descriptor_v2(
                    ctx_repo_base_url=src_ctx_repo_base_url,
                    component_name=component_descriptor.component.name,
                    component_version=component_descriptor.component.version,
                    absent_ok=True,
                ) is not None

                if cd_exists_in_src_ctx:
                    cnudie.replicate.replicate_oci_artifact_with_patched_component_descriptor(
                        src_ctx_repo_base_url=src_ctx_repo_base_url,
                        src_name=component_descriptor.component.name,
                        src_version=component_descriptor.component.version,
                        patched_component_descriptor=component_descriptor,
                        on_exist=upload_mode_cd,
                    )
                else:
                    if component.resources:
                        raise NotImplementedError('cannot replicate resources of root component')
                    product.v2.upload_component_descriptor_v2_to_oci_registry(
                        component_descriptor_v2=component_descriptor,
                        on_exist=upload_mode_cd,
                    )
            else:
                cnudie.replicate.replicate_oci_artifact_with_patched_component_descriptor(
                    src_ctx_repo_base_url=src_ctx_repo_base_url,
                    src_name=component_descriptor.component.name,
                    src_version=component_descriptor.component.version,
                    patched_component_descriptor=component_descriptor,
                    on_exist=upload_mode_cd,
                )
        elif processing_mode == ProcessingMode.DRY_RUN:
            print('dry-run - will not publish component-descriptor')
            return
        else:
            raise NotImplementedError(processing_mode)

    for _ in executor.map(reupload_component, components):
        pass

    # find the original component (yes, this is hacky / cumbersome)
    original_comp = [
        c for c in components
        if c.name == source_comp.name and c.version == source_comp.version
    ]
    if not (leng := len(original_comp)) == 1:
        if leng < 1:
            raise RuntimeError(f'did not find {source_comp.name=} - this is a bug!')
        if leng > 1:
            raise RuntimeError(f'found more than one version of {source_comp.name=} - pbly a bug!')

    return dataclasses.replace(
        component_descriptor_v2,
        component=original_comp[0],  # safe, because we check for leng above
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--component-descriptor')
    parser.add_argument('-p', '--processing-config', required=True)
    parser.add_argument('-d', '--dry-run', action='store_true')
    parser.add_argument('-t', '--tgt-ctx-repo-url', required=True)
    parser.add_argument('-s', '--src-ctx-repo-url')
    parser.add_argument('-n', '--component-name')
    parser.add_argument('-v', '--component-version')
    parser.add_argument('-l', '--skip-cd-validation', action='store_true')
    parser.add_argument('-g', '--rbsc-git-url')
    parser.add_argument('-b', '--rbsc-git-branch')
    parser.add_argument('-u', '--upload-mode-cd',
                        choices=[
                            mode.value for _, mode in product.v2.UploadMode.__members__.items()
                        ],
                        default=product.v2.UploadMode.SKIP.value
                        )
    parser.add_argument('-i', '--upload-mode-images',
                        choices=[
                            mode.value for _, mode in product.v2.UploadMode.__members__.items()
                        ],
                        default=product.v2.UploadMode.SKIP.value
                        )
    parser.add_argument('-r', '--replace-resource-tags-with-digests', action='store_true',
                        help='replace tags with digests for resources that are accessed via OCI references'
                        )

    parsed = parser.parse_args()

    if parsed.component_descriptor:
        component_descriptor = cm.ComponentDescriptor.from_dict(
            ci.util.parse_yaml_file(parsed.component_descriptor)
        )
    elif parsed.src_ctx_repo_url and parsed.component_name and parsed.component_version:
        src_ctx_repo_url = parsed.src_ctx_repo_url
        component_descriptor = cnudie.retrieve.component_descriptor(
            ctx_repo_url=parsed.src_ctx_repo_url,
            name=parsed.component_name,
            version=parsed.component_version,
        )

        if component_descriptor.component.current_repository_ctx().baseUrl != src_ctx_repo_url:
            component_descriptor.component.repositoryContexts.append(
                cm.OciRepositoryContext(
                    baseUrl=src_ctx_repo_url,
                    type=cm.AccessType.OCI_REGISTRY,
                ),
            )
    else:
        raise RuntimeError('you must either set --component-descriptor, or --src-ctx-repo-url, --component-name, and --component-version')

    tgt_ctx_repo_url = parsed.tgt_ctx_repo_url

    if parsed.dry_run:
        processing_mode = ProcessingMode.DRY_RUN
    else:
        processing_mode = ProcessingMode.REGULAR

    print(f'will now copy/patch specified component-descriptor to {tgt_ctx_repo_url=}')

    component_descriptor_v2 = process_images(
        parsed.processing_config,
        component_descriptor_v2=component_descriptor,
        tgt_ctx_base_url=tgt_ctx_repo_url,
        processing_mode=processing_mode,
        upload_mode_cd=product.v2.UploadMode(parsed.upload_mode_cd),
        upload_mode_images=product.v2.UploadMode(parsed.upload_mode_images),
        replace_resource_tags_with_digests=parsed.replace_resource_tags_with_digests,
        skip_cd_validation=parsed.skip_cd_validation,
    )

    if parsed.component_descriptor:
        with open(parsed.component_descriptor, 'w') as f:
            yaml.dump(
                data=dataclasses.asdict(component_descriptor_v2),
                stream=f,
                Dumper=cm.EnumValueYamlDumper,
            )

    if parsed.rbsc_git_url:
        if not parsed.rbsc_git_branch:
            raise ValueError('Please provide --rbsc-git-branch when using --rbsc-git-url')
        buildAndApplyBOM(
            parsed.rbsc_git_url,
            parsed.rbsc_git_branch,
            bom_resources,
        )


if __name__ == '__main__':
    main()
