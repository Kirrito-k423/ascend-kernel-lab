#pragma once
#include <cstdlib>
#include <cstring>
using aclError=int;
using aclrtEvent=int*;
constexpr int ACL_SUCCESS=0, ACL_EVENT_TIME_LINE=1, ACL_MEM_MALLOC_HUGE_FIRST=0, ACL_MEMCPY_DEVICE_TO_HOST=1;
inline int allocations=0, freed=0, fail_copy=0, fail_sync=0;
inline int aclrtCreateEventWithFlag(aclrtEvent* p,int) {*p=new int(0);return 0;}
inline int aclrtDestroyEvent(aclrtEvent p) {delete p;return 0;}
inline int aclrtRecordEvent(aclrtEvent,void*) {return 0;}
inline int aclrtSynchronizeEvent(aclrtEvent) {return fail_sync;}
inline int aclrtSynchronizeStream(void*) {return fail_sync;}
inline int aclrtEventElapsedTime(float* ms,aclrtEvent,aclrtEvent) {*ms=.125f;return 0;}
inline int aclrtMalloc(void** p,size_t n,int) {*p=std::malloc(n);++allocations;return *p?0:1;}
inline int aclrtFree(void* p) {std::free(p);++freed;return 0;}
inline int aclrtMemset(void* p,size_t,int v,size_t n) {std::memset(p,v,n);return 0;}
inline int aclrtMemcpy(void* p,size_t,const void* q,size_t n,int) {if(fail_copy)return 1;std::memcpy(p,q,n);return 0;}
