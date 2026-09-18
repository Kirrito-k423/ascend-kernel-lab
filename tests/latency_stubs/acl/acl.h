#pragma once
using aclError=int;
using aclrtEvent=int*;
constexpr int ACL_SUCCESS=0, ACL_EVENT_TIME_LINE=1;
inline int created=0,destroyed=0,recorded=0,synced=0,fail_create=0,fail_record=0;
inline int aclrtCreateEventWithFlag(aclrtEvent* p,int) { ++created;if(created==fail_create)return 1;*p=new int(created);return 0; }
inline int aclrtDestroyEvent(aclrtEvent p) {delete p;++destroyed;return 0;}
inline int aclrtRecordEvent(aclrtEvent,void*) {++recorded;return fail_record;}
inline int aclrtSynchronizeEvent(aclrtEvent) {++synced;return 0;}
inline int aclrtEventElapsedTime(float* ms,aclrtEvent,aclrtEvent) {*ms=.125f;return 0;}
