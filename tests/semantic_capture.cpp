// CPU 契约回归：真实 Capture + 可注入失败的 ACL 替身，不作为 NPU 实测。
#include <acl/acl.h>
#include <atomic>
#include <cassert>
#include <fstream>
#include <thread>
#include <vector>
std::atomic<int> allocated{0}, released{0};
bool fail_alloc=false, fail_copy=false;
int HostAlloc(void** p,size_t n) { if(fail_alloc)return 1; ++allocated; return aclrtMallocHost(p,n); }
int HostFree(void* p) { ++released; return aclrtFreeHost(p); }
int Copy(void* d,size_t m,const void* s,size_t n,int k) { return fail_copy?1:aclrtMemcpy(d,m,s,n,k); }
#define aclrtMallocHost HostAlloc
#define aclrtFreeHost HostFree
#define aclrtMemcpy Copy
#include "akl/trace/capture.h"
#undef aclrtMallocHost
#undef aclrtFreeHost
#undef aclrtMemcpy
namespace fs=std::filesystem;
std::vector<char> bytes(const fs::path& p) {
    std::ifstream in(p,std::ios::binary);
    return {std::istreambuf_iterator<char>(in),std::istreambuf_iterator<char>()};
}
void run(const fs::path& root,std::vector<uint64_t> counts,bool corrupt=false) {
    akl::Capture<256> capture(counts.size(),nullptr);
    auto* data=reinterpret_cast<uint64_t*>(capture.Data());
    std::vector<uint64_t> original(counts.size()*capture.words);
    for(size_t b=0;b<counts.size();++b) {
        auto* p=original.data()+b*capture.words;
        p[0]=akl::kMagic;p[1]=1;p[2]=counts[b];p[3]=b==1?1000:0;p[4]=b;p[5]=b%2;p[6]=b*7;p[7]=1;
        for(size_t i=0;i<counts[b];++i){p[8+2*i]=7;p[9+2*i]=(1ULL<<60)+b*10000+i;}
    }
    if(corrupt)original[capture.words]=0;
    std::memcpy(data,original.data(),original.size()*8);
    capture.Export(root,0);capture.Export(root,0);
    assert(std::memcmp(data,original.data(),original.size()*8)==0); // 紧凑排列不改设备缓冲。
    size_t files=0;
    for(auto& item:fs::directory_iterator(root)) {
        auto raw=bytes(item.path()/"trace.bin");size_t row=raw.size()/counts.size();
        auto meta=bytes(item.path()/"capture.json");std::string text(meta.begin(),meta.end());
        assert(text.find("\"recorder_capacity\":256")!=std::string::npos);
        for(size_t b=0;b<counts.size();++b) {
            assert(std::memcmp(raw.data()+b*row,original.data()+b*capture.words,row)==0);
            if(!corrupt)assert(row>=(8+2*counts[b])*8);
        }
        if(corrupt)assert(raw.size()==original.size()*8);
        ++files;
    }
    assert(files==2);
}
int main(int argc,char** argv) {
    assert(argc==2);fs::path root=argv[1];
    run(root/"mixed",{0,1,21,3});run(root/"empty",{0,0});run(root/"full",{256,17});
    run(root/"damaged",{3,2},true);
    std::thread a([&]{run(root/"thread-a",{1,3});}),b([&]{run(root/"thread-b",{21,8});});a.join();b.join();
    akl::Capture<256> capture(1,nullptr);
    auto expect_failure=[&](const fs::path& path){bool caught=false;try{capture.Export(path,0);}catch(const std::exception&){caught=true;}assert(caught);assert(allocated==released);};
    fail_alloc=true;expect_failure(root/"alloc-failure");fail_alloc=false;
    fail_copy=true;expect_failure(root/"copy-failure");fail_copy=false;
    std::ofstream(root/"not-directory").put('x');expect_failure(root/"not-directory");
    assert(allocated==released);
}
