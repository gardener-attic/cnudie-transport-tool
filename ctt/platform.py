# SPDX-FileCopyrightText: 2022 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import re
import typing

import oci.model as om


class PlatformFilter:
    @staticmethod
    def create(
        included_platforms: typing.List[str],
    ) -> typing.Callable[[om.OciPlatform], bool]:
        '''
        @param included_platforms: a list of regexes to filter for platforms in format os/arch/variant
        '''
        def filter(platform_to_match: om.OciPlatform) -> bool:
            normalised_platform = normalise(platform_to_match)
            for regex_platform in included_platforms:
                if re.fullmatch(regex_platform, normalised_platform):
                    return True

            return False

        return filter


def normalise(p: om.OciPlatform):
    os = normalise_os(p.os)
    arch, variant = normalise_arch(p.architecture, p.variant)

    normalised = os + '/' + arch
    if not variant == '':
        normalised += '/' + variant

    return normalised


def normalise_os(os: str) -> str:
    '''
    https://github.com/containerd/containerd/blob/8686ededfc90076914c5238eb96c883ea093a8ba/platforms/database.go#L69
    '''
    if not os:
        raise ValueError(os)

    os = os.lower()
    if os == 'macos':
        os = 'darwin'

    return os


def normalise_arch(arch: str, variant: str) -> typing.Tuple:
    '''
    https://github.com/containerd/containerd/blob/8686ededfc90076914c5238eb96c883ea093a8ba/platforms/database.go#L83
    '''
    if not arch:
        raise ValueError(arch)

    variant = variant or ''
    arch, variant = arch.lower(), variant.lower()
    match arch:
        case 'i386':
            arch = '386'
            variant = ''
        case 'x86_64', 'x86-64':
            arch = 'amd64'
            variant = ''
        case 'aarch64', 'arm64':
            arch = 'arm64'
            if variant in ('8', 'v8'):
                variant = ''
        case 'armhf':
            arch = 'arm'
            variant = 'v7'
        case 'armel':
            arch = 'arm'
            variant = 'v6'
        case 'arm':
            if variant in ('', '7'):
                variant = 'v7'
            elif variant in ('5', '6', '8'):
                variant = 'v' + variant

    return arch, variant
