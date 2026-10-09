cmake_minimum_required(VERSION 3.20)
set(CANN "$ENV{ASCEND_HOME_PATH}")
set(CMAKE_CXX_COMPILER "${CANN}/bin/bisheng")
project(akl_network LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
set(SHMEM_ROOT "" CACHE PATH "匹配动态库的 SHMEM 源码目录")
set(SHMEM_LIB "" CACHE FILEPATH "固定版本 libshmem.so")
if(NOT EXISTS "${SHMEM_ROOT}/include/shmem.h" OR NOT EXISTS "${SHMEM_LIB}")
  message(FATAL_ERROR "必须指定 SHMEM_ROOT 和 SHMEM_LIB")
endif()
get_filename_component(SHMEM_LIB_DIR "${SHMEM_LIB}" DIRECTORY)
add_executable(akl_network main.cpp kernel.cpp)
target_include_directories(akl_network PRIVATE "${CANN}/include" "${SHMEM_ROOT}/include"
  "${SHMEM_ROOT}/src" "${SHMEM_ROOT}/src/device" "${SHMEM_ROOT}/src/device_simt")
target_compile_options(akl_network PRIVATE -O2 -g)
set_source_files_properties(kernel.cpp PROPERTIES COMPILE_OPTIONS "-xasc;--npu-arch=dav-3510;-save-temps")
target_link_options(akl_network PRIVATE --cce-fatobj-link)
target_link_directories(akl_network PRIVATE "${CANN}/lib64")
target_link_libraries(akl_network PRIVATE "${SHMEM_LIB}" ascendcl runtime dl)
set_target_properties(akl_network PROPERTIES BUILD_RPATH "${CANN}/lib64;${SHMEM_LIB_DIR}")
# 使用规范化路径键，使不同主机的结果可按内容校验。
add_custom_command(TARGET akl_network POST_BUILD
  COMMAND python3 "${CMAKE_CURRENT_SOURCE_DIR}/make_receipt.py"
  --binary "$<TARGET_FILE:akl_network>" --lib "${SHMEM_LIB}" --sdk "${SHMEM_ROOT}"
  --output "${CMAKE_CURRENT_BINARY_DIR}/fingerprints.txt")
