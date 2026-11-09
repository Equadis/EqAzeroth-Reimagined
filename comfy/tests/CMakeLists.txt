# Builds the dense-grass config test against the patched ComfyAtmosphere source (32-bit, like the DLL).
#   cmake -S comfy/tests -B build-test -A Win32 -DCOMFY_SRC=<path to the patched comfyatmosphere checkout>
cmake_minimum_required(VERSION 3.15)
project(grass_config_test CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT COMFY_SRC)
    message(FATAL_ERROR "pass -DCOMFY_SRC=<comfyatmosphere checkout>")
endif()
add_executable(grass_config_test grass_config_test.cpp ${COMFY_SRC}/src/config.cpp)
target_include_directories(grass_config_test PRIVATE ${COMFY_SRC}/src)
if(MSVC)
    target_compile_options(grass_config_test PRIVATE /W3 /permissive-)
endif()
