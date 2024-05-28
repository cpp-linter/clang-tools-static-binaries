#!/bin/bash

# This script builds clang tools for a specified version
# Usage: ./build.sh <clang-version>

set -e

# Check if cmake is installed
if ! command -v cmake &> /dev/null; then
  echo "Error: cmake is not installed."
  exit 1
fi

CLANG_VERSION=$1
OS=$(uname | tr '[:upper:]' '[:lower:]')
RELEASE=""
COMMON_CMAKE_ARGS='-DBUILD_SHARED_LIBS=OFF -DLLVM_ENABLE_PROJECTS="clang;clang-tools-extra"'
POSIX_CMAKE_ARGS='-DCMAKE_BUILD_TYPE=MinSizeRel -DCMAKE_CXX_COMPILER=g++-11 -DCMAKE_C_COMPILER=gcc-11'

# Determine release based on the clang version
case $CLANG_VERSION in
  18) RELEASE="llvm-project-18.1.5.src" ;;
  17) RELEASE="llvm-project-17.0.4.src" ;;
  16) RELEASE="llvm-project-16.0.3.src" ;;
  15) RELEASE="llvm-project-15.0.2.src" ;;
  14) RELEASE="llvm-project-14.0.0.src" ;;
  13) RELEASE="llvm-project-13.0.0.src" ;;
  12.0.1) RELEASE="llvm-project-12.0.1.src" ;;
  12) RELEASE="llvm-project-12.0.0.src" ;;
  11) RELEASE="llvm-project-11.1.0.src" ;;
  10) RELEASE="llvm-project-10.0.1" ;;
  9) RELEASE="llvm-project-9.0.1" ;;
  8) RELEASE="llvm-project-8.0.1" ;;
  7) RELEASE="llvm-project-7.1.0" ;;
  *) echo "Unsupported clang version: $CLANG_VERSION" ; exit 1 ;;
esac

# Determine OS-specific configurations
case $OS in
  linux)
    RUNNER="ubuntu-22.04"
    OS_CMAKE_ARGS='-DLLVM_BUILD_STATIC=ON -DCMAKE_CXX_FLAGS="-s -flto" ${POSIX_CMAKE_ARGS}'
    BUILD_ARGS='-j$(nproc)'
    BINDIR='/build/bin'
    DOTEXE=''
    SHACMD='sha512sum'
    ;;
  darwin)
    RUNNER="macos-14"
    OS_CMAKE_ARGS='-DCMAKE_CXX_FLAGS="-static-libgcc -static-libstdc++ -flto" -DCMAKE_OSX_DEPLOYMENT_TARGET=11 ${POSIX_CMAKE_ARGS}'
    BUILD_ARGS='-j$(sysctl -n hw.ncpu)'
    BINDIR='/build/bin'
    DOTEXE=''
    SHACMD='shasum -a 512'
    ;;
  msys*|cygwin*|mingw*)
    RUNNER="windows-latest"
    OS_CMAKE_ARGS='-Thost=x64 -DCMAKE_CXX_FLAGS="/MP /std:c++14" -DLLVM_USE_CRT_MINSIZEREL="MT"'
    BUILD_ARGS='--config MinSizeRel'
    BINDIR='/build/MinSizeRel/bin'
    DOTEXE='.exe'
    SHACMD='sha512sum.exe'
    ;;
  *)
    echo "Unsupported OS: $OS"
    exit 1
    ;;
esac

SUFFIX="${CLANG_VERSION}_${OS}-amd64"

# Download llvm-project
download_llvm_project() {
  VERSION=${RELEASE##llvm-project-}; VERSION=${VERSION%.src}
  curl -LO https://github.com/llvm/llvm-project/releases/download/llvmorg-${VERSION}/${RELEASE}.tar.xz
}

# Unpack llvm-project
unpack_llvm_project() {
  tar xf ${RELEASE}.tar.xz
}

# Apply patches based on version
apply_patches() {
  if [[ $CLANG_VERSION == 8 ]]; then
    patch ${RELEASE}/llvm/include/llvm/Demangle/MicrosoftDemangleNodes.h include-cstdint-string-prior-to-using-uint8_t.patch
  elif [[ $CLANG_VERSION == 9 || $CLANG_VERSION == 10 && $OS == 'windows' ]]; then
    patch ${RELEASE}/llvm/cmake/config-ix.cmake windows-clang-9-10-trivially-copyable-mismatch.patch
  fi

  if [[ $OS == 'darwin' ]]; then
    sed -i.backup 's/gcc_eh.\*|/gcc_eh.*|gcc_ext.*|/g' $(find /opt/homebrew/Cellar -name CMakeParseImplicitLinkInfo.cmake)
  fi
}

# Run CMake and build
build_clang_tools() {
  cmake -S ${RELEASE}/llvm -B ${RELEASE}/build ${COMMON_CMAKE_ARGS} ${OS_CMAKE_ARGS}
  cmake --build ${RELEASE}/build ${BUILD_ARGS} --target clang-format clang-query clang-tidy clang-apply-replacements
}

# Rename and checksum binaries
rename_and_checksum() {
  cd ${RELEASE}${BINDIR}
  for tool in clang-format clang-query clang-tidy clang-apply-replacements; do
    mv ${tool}${DOTEXE} ${tool}-${SUFFIX}${DOTEXE}
    $SHACMD ${tool}-${SUFFIX}${DOTEXE} > ${tool}-${SUFFIX}${DOTEXE}.sha512sum
    echo "Checksums for ${tool}-${SUFFIX}${DOTEXE}:"
    cat ${tool}-${SUFFIX}${DOTEXE}.sha512sum
  done
}

# Main
download_llvm_project
unpack_llvm_project
apply_patches
build_clang_tools
rename_and_checksum
