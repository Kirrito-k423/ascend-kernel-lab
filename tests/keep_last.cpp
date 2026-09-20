// CPU ACL 替身，只检验文件生命周期；不是 NPU 实测。
#include "akl/trace/capture.h"
#include <cassert>
#include <iostream>
#include <thread>
using namespace std::filesystem;
void fill(akl::Capture<4>& capture, bool valid = true) {
    auto* row = reinterpret_cast<uint64_t*>(capture.Data());
    const uint64_t values[] = {valid ? akl::kMagic : 0, 1, 1, 0, 0, 0, 0, 1, 7, (1ULL << 60)};
    std::copy(std::begin(values), std::end(values), row);
}
size_t count(const path& root) {
    size_t total = 0;
    for (const auto& item : recursive_directory_iterator(root)) if (item.path().filename()=="trace.bin") ++total;
    return total;
}
int main(int argc, char** argv) {
    assert(argc==2); const path root = argv[1]; create_directories(root);
    unsetenv("AKL_TRACE_KEEP_LAST");
    for (int i=0; i<3; ++i) { akl::Capture<4> c(1,nullptr); fill(c); c.Export(root/"all",0); }
    assert(count(root/"all")==3);
    setenv("AKL_TRACE_KEEP_LAST","1",1);
    for (uint32_t rank=0; rank<2; ++rank)
        for (int i=0; i<20; ++i) { akl::Capture<4> c(1,nullptr); fill(c); c.Export(root/"last",rank); }
    assert(count(root/"last")==2);
    // 业务文件保留；清理只涉及本进程登记的旧二进制与 metadata。
    for (const auto& folder : directory_iterator(root/"last")) std::ofstream(folder.path()/"notes.txt") << "keep";
    { akl::Capture<4> c(1,nullptr); fill(c); c.Export(root/"last",0); }
    assert(count(root/"last")==2);
    size_t notes=0; for (const auto& f : recursive_directory_iterator(root/"last")) if(f.path().filename()=="notes.txt") ++notes;
    assert(notes==2);
    // 损坏记录不替换上一个完整采集。
    { akl::Capture<4> c(1,nullptr); fill(c,false); c.Export(root/"last",0); }
    assert(count(root/"last")==3);
    { std::ofstream(root/"not-a-directory") << "keep"; akl::Capture<4> c(1,nullptr); fill(c);
      bool failed=false; try {c.Export(root/"not-a-directory",0);} catch(const filesystem_error&) {failed=true;}
      assert(failed); assert(count(root/"last")==3); }
    auto write = [&root]() { for(int i=0;i<10;++i) {akl::Capture<4> c(1,nullptr);fill(c);c.Export(root/"concurrent",0);} };
    std::thread a(write), b(write); a.join(); b.join(); assert(count(root/"concurrent")==1);
    const path older=root/"reversed/rank0-pid42-launch1", newer=root/"reversed/rank0-pid42-launch2";
    for(const auto& p : {older,newer}) {create_directories(p);std::ofstream(p/"trace.bin") << "data";std::ofstream(p/"capture.json") << "meta";}
    akl::detail::KeepLastCapture(newer,2); akl::detail::KeepLastCapture(older,1);
    assert(exists(newer/"trace.bin")); assert(!exists(older));
    std::cout << "PASS: default all; 20 exports x 2 ranks retain 2; corrupt/write failure retain prior; concurrent/reversed completion; user files preserved\n";
}
