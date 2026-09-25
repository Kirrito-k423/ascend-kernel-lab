#pragma once
#include <cstdlib>
#include <cstring>
#include <cstdint>
using aclError = int;
constexpr int ACL_SUCCESS=0, ACL_MEM_MALLOC_HUGE_FIRST=0, ACL_MEMCPY_DEVICE_TO_HOST=0;
inline int aclrtGetDevice(int32_t* device) { *device=0; return 0; }
inline int aclrtMalloc(void** p, size_t bytes, int) { *p=std::malloc(bytes); return *p?0:1; }
inline int aclrtMemset(void* p, size_t, int v, size_t bytes) { std::memset(p,v,bytes); return 0; }
inline int aclrtSynchronizeStream(void*) { return 0; }
inline int aclrtMemcpy(void* d, size_t, const void* s, size_t bytes, int) { std::memcpy(d,s,bytes); return 0; }
inline int aclrtFree(void* p) { std::free(p); return 0; }

inline int aclrtMallocHost(void** p, size_t bytes) { *p=std::malloc(bytes); return *p ? 0 : 1; }
inline int aclrtFreeHost(void* p) { std::free(p); return 0; }
