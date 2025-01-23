import argparse
import subprocess
import os

# Constants for configuration
COMMON_CMAKE_ARGS = (
    '-DBUILD_SHARED_LIBS=OFF -DLLVM_ENABLE_PROJECTS="clang;clang-tools-extra"'
)
MACOS_CMAKE_ARGS = "-DCMAKE_BUILD_TYPE=MinSizeRel -DCMAKE_CXX_COMPILER=g++-11 -DCMAKE_C_COMPILER=gcc-11 -DZSTD_STATIC_LINKING_ONLY=1"
LINUX_CMAKE_ARGS = "-DCMAKE_BUILD_TYPE=MinSizeRel -DCMAKE_CXX_COMPILER=g++-10 -DCMAKE_C_COMPILER=gcc-10"

BUILD_CONFIG = {
    "linux": {
        "cmake_args": f'-DLLVM_BUILD_STATIC=ON -DCMAKE_CXX_FLAGS="-s -flto" {LINUX_CMAKE_ARGS}',
        "build_args": "-j$(nproc)",
        "bindir": "/build/bin",
        "dotexe": "",
        "shacmd": "sha512sum",
    },
    "macosx": {
        "cmake_args": f'-DCMAKE_CXX_FLAGS="-static-libgcc -static-libstdc++ -flto -ffunction-sections -fdata-sections" -DCMAKE_OSX_DEPLOYMENT_TARGET=11 {MACOS_CMAKE_ARGS}',
        "build_args": "-j$(sysctl -n hw.ncpu)",
        "bindir": "/build/bin",
        "dotexe": "",
        "shacmd": "shasum -a512",
    },
    "windows": {
        "cmake_args": '-Thost=x64 -DCMAKE_CXX_FLAGS="/MP /std:c++14" -DLLVM_USE_CRT_MINSIZEREL="MT"',
        "build_args": "--config MinSizeRel",
        "bindir": "/build/MinSizeRel/bin",
        "dotexe": ".exe",
        "shacmd": "sha512sum.exe",
    },
}

PLATFORMS = ["linux", "macosx", "windows"]

VERSION_TAG_MAPPING = {
    "7": "7.0.1",
    "8": "8.0.1",
    "9": "9.0.1",
    "10": "10.0.1",
    "11": "11.0.1",
    "12": "12.0.1",
    "13": "13.0.1",
    "14": "14.0.1",
    "15": "15.0.1",
    "16": "16.0.1",
    "17": "17.0.1",
    "18": "18.1.8",
    "19": "19.1.7",
}

RELEASE_URL = "https://github.com/llvm/llvm-project/releases/download"


def get_llvm_project(version, tag):
    print(f"Getting llvm-project for version {version}...")
    print(f"{RELEASE_URL}/llvmorg-{tag}/llvm-project-{tag}.src.tar.xz")

    if version in ["7", "8"]:
        subprocess.run(
            f"curl -LO {RELEASE_URL}/llvmorg-{tag}/llvm-{tag}.src.tar.xz",
            shell=True,
            check=True,
        )
        subprocess.run(
            f"curl -LO {RELEASE_URL}/llvmorg-{tag}/cfe-{tag}.src.tar.xz",
            shell=True,
            check=True,
        )
        subprocess.run(
            f"curl -LO {RELEASE_URL}/llvmorg-{tag}/clang-tools-extra-{tag}.src.tar.xz",
            shell=True,
            check=True,
        )
    else:
        subprocess.run(
            f"curl -LO {RELEASE_URL}/llvmorg-{tag}/llvm-project-{tag}.src.tar.xz",
            shell=True,
            check=True,
        )


def unpack_llvm_project(version, tag):
    print(f"Unpacking llvm-project for version {version}...")
    if version in ["7", "8"]:
        subprocess.run(f"tar xf llvm-{tag}.src.tar.xz", shell=True, check=True)
        subprocess.run(f"tar xf cfe-{tag}.src.tar.xz", shell=True, check=True)
        subprocess.run(
            f"tar xf clang-tools-extra-{tag}.src.tar.xz", shell=True, check=True
        )
    else:
        subprocess.run(f"tar xf llvm-project-{tag}.src.tar.xz", shell=True, check=True)


def build_project(version, platform):
    config = BUILD_CONFIG[platform]
    cmake_args = config["cmake_args"]
    build_args = config["build_args"]

    print(f"Building LLVM project version {version} on platform {platform}...")
    os.makedirs(f"llvm-{version}/build", exist_ok=True)
    subprocess.run(
        f"cmake -S llvm-{version} -B llvm-{version}/build {COMMON_CMAKE_ARGS} {cmake_args}",
        shell=True,
        check=True,
    )
    subprocess.run(
        f"cmake --build llvm-{version}/build {build_args}", shell=True, check=True
    )


def main():
    parser = argparse.ArgumentParser(
        description="Build LLVM for multiple versions and platforms."
    )
    parser.add_argument(
        "-v", "--version", nargs="*", help="Specify LLVM versions to build."
    )
    parser.add_argument(
        "-p",
        "--platform",
        nargs="*",
        choices=PLATFORMS,
        help="Specify platforms to build for.",
    )

    args = parser.parse_args()
    versions = args.version or sorted(VERSION_TAG_MAPPING.keys(), key=int)
    platforms = args.platform or PLATFORMS

    for version in versions:
        if version not in VERSION_TAG_MAPPING:
            print(f"Skipping unknown version: {version}")
            continue

        tag = VERSION_TAG_MAPPING[version]
        for version in versions:
            # release = f"llvm-project-{tag}.src"
            get_llvm_project(version, tag)
            unpack_llvm_project(version, tag)
            for platform in platforms:
                build_project(version, platform)


if __name__ == "__main__":
    main()
