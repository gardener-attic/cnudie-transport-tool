# SPDX-FileCopyrightText: 2022 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import logging
import os
import subprocess
import tempfile
import typing

import ci.log
import ci.util
import oci.model as om


cosign_password_env = 'COSIGN_PASSWORD'
cosign_private_key_filename = 'import-cosign.key'
cosign_public_key_filename = 'import-cosign.pub'


ci.log.configure_default_logging()
logger = logging.getLogger(__name__)


# generates and uploads the cosign signature for a target image ref
# the target image must be referenced via digest, as cosign generates the signature ref based on the target image digest
# returns the cosign signature ref
def generate_cosign_signature(
    img_ref: str,
    key_file: str,
) -> str:
    parsed_img_ref = om.OciImageReference.to_image_ref(img_ref)
    if not parsed_img_ref.has_digest_tag:
        NotImplementedError('only images that are referenced via a digest are allowed')

    env = os.environ.copy()
    # set special env variable to disable password prompt from cosign
    env[cosign_password_env] = ''

    signCmd = f'cosign sign --key {key_file} {img_ref}'
    logger.info(f'run cmd \'{signCmd}\'')
    subprocess.run(signCmd.split(' '), check=True, env=env)

    parsed_digest = parsed_img_ref.parsed_digest_tag
    digest_algo = parsed_digest[0]
    digest_val = parsed_digest[1]
    cosign_sig_ref = f'{parsed_img_ref.ref_without_tag}:{digest_algo}-{digest_val}.sig'

    return cosign_sig_ref


# import a PEM-encoded RSA or EC private key via cosign from the file system
# returns the path to the generated cosign private and public key files
# the files must be cleaned up by the caller once they are no longer needed
def import_key_pair_from_file(
    private_key_file: str,
) -> typing.Tuple[str, str]:
    abs_private_key_file = os.path.abspath(private_key_file)

    tmpdir = tempfile.mkdtemp()
    cwd = os.getcwd()
    os.chdir(tmpdir)

    env = os.environ.copy()
    # set special env variable to disable password prompt from cosign
    env[cosign_password_env] = ''

    signCmd = f'cosign import-key-pair --key {abs_private_key_file}'
    logger.info(f'run cmd \'{signCmd}\'')
    subprocess.run(signCmd.split(' '), check=True, env=env)

    os.chdir(cwd)

    cosign_private_key_file = os.path.join(tmpdir, cosign_private_key_filename)
    cosign_public_key_file = os.path.join(tmpdir, cosign_public_key_filename)

    return (cosign_private_key_file, cosign_public_key_file)


# import a PEM-encoded RSA or EC private key via cosign from memory
# returns the path to the generated cosign private and public key files
# the files must be cleaned up by the caller once they are no longer needed
def import_key_pair_from_memory(private_key: str) -> typing.Tuple[str, str]:
    with tempfile.NamedTemporaryFile() as f:
        f.write(private_key.encode())
        f.seek(0)
        return import_key_pair_from_file(f.name)
