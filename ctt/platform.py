# SPDX-FileCopyrightText: 2022 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import enum
import typing

import oci.model as om

class OperatingSystem(enum.Enum):
    '''
    OperatingSystem contains the values for the 'os' property in an oci multiarch image.
    See https://go.dev/doc/install/source#environment.
    '''
    AIX = 'aix'
    ANDROID = 'android'
    DARWIN = 'darwin'
    DRAGONFLY = 'dragonfly'
    FREEBSD = 'freebsd'
    ILLUMOS = 'illumos'
    IOS = 'ios'
    JS = 'js'
    LINUX = 'linux'
    NETBSD = 'netbsd'
    OPENBSD = 'openbsd'
    PLAN9 = 'plan9'
    SOLARIS = 'solaris'
    WINDOWS = 'windows'


class Architecture(enum.Enum):
    '''
    Architecture contains the values for the 'architecture' property in an oci multiarch image.
    See https://go.dev/doc/install/source#environment.
    '''
    PPC64 = 'ppc64'
    _386 = '386'
    AMD64 = 'amd64'
    ARM = 'arm'
    ARM64 = 'arm64'
    WASM = 'wasm'
    LOONG64 = 'loong64'
    MIPS = 'mips'
    MIPSLE = 'mipsle'
    MIPS64 = 'mips64'
    MIPS64LE = 'mips64le'
    PPC64le = 'ppc64le'
    RISCV64 = 'riscv64'
    S390X = 's390x'


class PlatformFilter:
    @staticmethod
    def create(
        included_platforms: typing.List[str],
    ) -> typing.Callable[[om.OciPlatform], bool]:
        matchers = [PlatformFilter._parse_expr(included_platform) for included_platform in included_platforms ]

        def filter(platform_to_match: om.OciPlatform) -> bool:
            for m in matchers:
                normalised_p = normalise(platform_to_match)
                if ((m['os'] == '*' or m['os'] == normalised_p.os) and
                    (m['architecture'] == '*' or m['architecture'] == normalised_p.architecture) and
                    (m['variant'] == '*' or m['variant'] == normalised_p.variant)):
                    return True

            return False

        return filter

    @staticmethod
    def _parse_expr(platform_expr: str) -> dict:
        splitted = platform_expr.split('/')
        match len(splitted):
            case 2:
                os, architecture = splitted
                variant = '*'
            case 3:
                os, architecture, variant = splitted
            case _:
                raise ValueError(f'{platform_expr=} - invalid length {len(splitted)} of splitted'
                    ' oci platform expression. length must be either 2 or 3. please check that the'
                    ' expression has the format os/architecture[/variant]')

        if os != '*': OperatingSystem(os)
        if architecture != '*': Architecture(architecture)

        return {
            'os': os,
            'architecture': architecture,
            'variant': variant,
        }


def normalise(p: om.OciPlatform):
    os = normalise_os(p.os)
    arch, variant = normalise_arch(p.architecture, p.variant)

    return om.OciPlatform(
        os=os,
        architecture=arch,
        variant=variant
    )


def normalise_os(os: str) -> str:
    '''
    https://github.com/containerd/containerd/blob/8686ededfc90076914c5238eb96c883ea093a8ba/platforms/database.go#L69
    '''
    os = os or ''

    os = os.lower()
    match os:
        case "macos":
            os = "darwin"

    return os


def normalise_arch(arch: str, variant: str) -> typing.Tuple:
    '''
    https://github.com/containerd/containerd/blob/8686ededfc90076914c5238eb96c883ea093a8ba/platforms/database.go#L83
    '''
    arch = arch or ''
    variant = variant or ''

    arch, variant = arch.lower(), variant.lower()
    match arch:
        case "i386":
            arch = "386"
            variant = ""
        case "x86_64", "x86-64":
            arch = "amd64"
            variant = ""
        case "aarch64", "arm64":
            arch = "arm64"
            match variant:
                case "8", "v8":
                    variant = ""
        case "armhf":
            arch = "arm"
            variant = "v7"
        case "armel":
            arch = "arm"
            variant = "v6"
        case "arm":
            match variant:
                case "", "7":
                    variant = "v7"
                case "5", "6", "8":
                    variant = "v" + variant

    return (arch, variant)