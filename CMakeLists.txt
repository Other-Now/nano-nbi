cmake_minimum_required(VERSION 3.16)
project(nano-nbi C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
add_compile_options(-Wall -Wextra -Werror=implicit-function-declaration)

option(NN_ASAN "Build with AddressSanitizer" OFF)
if(NN_ASAN)
  add_compile_options(-fsanitize=address,undefined -fno-omit-frame-pointer)
  add_link_options(-fsanitize=address,undefined)
endif()

find_package(PkgConfig REQUIRED)
pkg_check_modules(SYSREPO REQUIRED IMPORTED_TARGET sysrepo)
pkg_check_modules(LIBYANG REQUIRED IMPORTED_TARGET libyang)
find_package(Threads REQUIRED)

add_executable(tunneld src/tunneld.c src/link.c)
target_link_libraries(tunneld PkgConfig::SYSREPO PkgConfig::LIBYANG Threads::Threads)
