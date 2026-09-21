cmake_minimum_required(VERSION 3.20)
project(jev)
set(CMAKE_CXX_STANDARD 17)
find_package(CURL REQUIRED)
include_directories(src/include)
build_loadable_extension(jev "" src/jev_extension.cpp)
target_link_libraries(jev_loadable_extension CURL::libcurl)
if(JEV_PREBUILT_DUCKDB_STATIC_DIR)
  target_link_libraries(jev_loadable_extension
                        "${JEV_PREBUILT_DUCKDB_STATIC_DIR}/libduckdb_static.a")
  if(UNIX AND NOT APPLE)
    set_target_properties(jev_loadable_extension
                          PROPERTIES CXX_VISIBILITY_PRESET hidden)
    target_link_options(jev_loadable_extension PRIVATE -Wl,--gc-sections
                        -Wl,--exclude-libs,ALL)
  endif()
endif()
