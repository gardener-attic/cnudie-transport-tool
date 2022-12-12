#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2021 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import argparse
import dataclasses
import textwrap
import typing

import ci.util
import cnudie.replicate
import cnudie.retrieve
import gci.componentmodel as cm
import oci
import product.v2
import yaml

from ctt.rbsc_bom import BOMEntry, buildAndApplyBOM
import ctt.platform as platform
import ctt.process_dependencies


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
    parser.add_argument(
        '--generate-cosign-signatures',
        action='store_true',
        help='generate cosign signatures for copied oci image resources'
    )
    parser.add_argument(
        '--cosign-repository',
        help='oci repository where cosign signatures should be stored'
    )
    parser.add_argument(
        '--signing-server-url',
        help='url of the signing server which is used for generating cosign signatures'
    )
    parser.add_argument(
        '--root-ca-cert',
        help=textwrap.dedent(
            '''\
            path to a file which contains the root ca cert in pem format for verifying
            the signing server tls certificate '''
        ),
    )
    parser.add_argument(
        '-u', '--upload-mode-cd',
        choices=[
            mode.value for _, mode in product.v2.UploadMode.__members__.items()
        ],
        default=product.v2.UploadMode.SKIP.value,
    )
    parser.add_argument(
        '-i', '--upload-mode-images',
        choices=[
            mode.value for _, mode in product.v2.UploadMode.__members__.items()
        ],
        default=product.v2.UploadMode.SKIP.value,
    )
    parser.add_argument(
        '-r', '--replace-resource-tags-with-digests',
        action='store_true',
        help='replace tags with digests for resources that are accessed via OCI references',
    )
    parser.add_argument(
        '--replication-mode',
        help='replication mode for OCI resources',
        choices=[
            mode.value for _, mode in oci.ReplicationMode.__members__.items()
        ],
        default=oci.ReplicationMode.PREFER_MULTIARCH,
    )
    parser.add_argument(
        '--included-platforms',
        help=textwrap.dedent('''
            list of platforms that should be copied for multiarch images.
            if the flag is omitted, every platform is copied. each list item
            must be a regex in the format os/architecture/variant.
            allowed values for os and architecture can be found here:
            https://go.dev/doc/install/source#environment.
            '''),
        nargs='*',
    )

    parsed = parser.parse_args()

    if parsed.component_descriptor:
        component_descriptor = cm.ComponentDescriptor.from_dict(
            ci.util.parse_yaml_file(parsed.component_descriptor)
        )
        component_descriptor_lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
            default_ctx_repo=component_descriptor.component.current_repository_ctx(),
        )
    elif parsed.src_ctx_repo_url and parsed.component_name and parsed.component_version:
        src_ctx_repo_url = parsed.src_ctx_repo_url
        ctx_repo = cm.OciRepositoryContext(
            baseUrl=src_ctx_repo_url,
        )
        component_descriptor_lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
            default_ctx_repo=ctx_repo,
        )
        component_descriptor = component_descriptor_lookup(cm.ComponentIdentity(
            name=parsed.component_name,
            version=parsed.component_version,
        ))

        if component_descriptor.component.current_repository_ctx().baseUrl != src_ctx_repo_url:
            component_descriptor.component.repositoryContexts.append(
                cm.OciRepositoryContext(
                    baseUrl=src_ctx_repo_url,
                    type=cm.AccessType.OCI_REGISTRY,
                ),
            )
    else:
        raise RuntimeError(
            'either set --component-descriptor, or all of --src-ctx-repo-url, --component-name, --component-version'
        )

    tgt_ctx_repo_url = parsed.tgt_ctx_repo_url

    if parsed.dry_run:
        processing_mode = ctt.process_dependencies.ProcessingMode.DRY_RUN
    else:
        processing_mode = ctt.process_dependencies.ProcessingMode.REGULAR

    platform_filter = None
    if parsed.included_platforms:
        platform_filter = platform.PlatformFilter.create(
            included_platforms=parsed.included_platforms,
        )

    if parsed.rbsc_git_url and not parsed.rbsc_git_branch:
        raise ValueError('Please provide --rbsc-git-branch when using --rbsc-git-url')

    print(f'will now copy/patch specified component-descriptor to {tgt_ctx_repo_url=}')

    bom_resources: typing.Sequence[BOMEntry] = []

    component_descriptor_v2 = ctt.process_dependencies.process_images(
        parsed.processing_config,
        component_descriptor_v2=component_descriptor,
        tgt_ctx_base_url=tgt_ctx_repo_url,
        processing_mode=processing_mode,
        upload_mode_cd=product.v2.UploadMode(parsed.upload_mode_cd),
        upload_mode_images=product.v2.UploadMode(parsed.upload_mode_images),
        replication_mode=oci.ReplicationMode(parsed.replication_mode),
        replace_resource_tags_with_digests=parsed.replace_resource_tags_with_digests,
        skip_cd_validation=parsed.skip_cd_validation,
        generate_cosign_signatures=parsed.generate_cosign_signatures,
        cosign_repository=parsed.cosign_repository,
        signing_server_url=parsed.signing_server_url,
        root_ca_cert_path=parsed.root_ca_cert,
        platform_filter=platform_filter,
        bom_resources=bom_resources,
        component_descriptor_lookup=component_descriptor_lookup,
    )

    if parsed.component_descriptor:
        with open(parsed.component_descriptor, 'w') as f:
            yaml.dump(
                data=dataclasses.asdict(component_descriptor_v2),
                stream=f,
                Dumper=cm.EnumValueYamlDumper,
            )

    if parsed.rbsc_git_url:
        buildAndApplyBOM(
            parsed.rbsc_git_url,
            parsed.rbsc_git_branch,
            bom_resources,
        )


if __name__ == '__main__':
    main()
