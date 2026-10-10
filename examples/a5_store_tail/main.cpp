#include <acl/acl.h>
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <vector>
#include <array>
#include <random>

extern "C" void launch_bandwidth(void*,void*,void*,void*,uint32_t,uint32_t,uint32_t,
    uint32_t,uint32_t,uint64_t,uint32_t,uint32_t,uint32_t,uint32_t);
static void Check(aclError rc,const char* action) {
    if(rc)throw std::runtime_error(std::string(action)+" ACL="+std::to_string(rc));
}
#define AC(call) Check((call),#call)
struct Resources {
    int device; bool initialized=false,selected=false;
    void *x=nullptr,*out=nullptr,*ticks=nullptr;
    aclrtStream stream=nullptr; aclrtEvent begin=nullptr,end=nullptr;
    ~Resources(){
        if(stream)aclrtSynchronizeStream(stream);
        for(void* p:{x,out,ticks})if(p)aclrtFree(p);
        if(begin)aclrtDestroyEvent(begin);if(end)aclrtDestroyEvent(end);
        if(stream)aclrtDestroyStream(stream);
        if(selected)aclrtResetDevice(device);if(initialized)aclFinalize();
    }
};
struct Case {
    uint32_t id,direction,shared,cores,tile,batch,control,groups;
    uint64_t requestedRing,targetBytes,perCoreRing,actualRing,outputBytes,movedBytes;
};
static uint64_t Ceil(uint64_t n,uint64_t unit){return (n+unit-1)/unit*unit;}
static uint32_t InputValue(uint64_t index){return 0x13579bdfu ^ (uint32_t(index)*2654435761u);}
static std::vector<Case> Plan(const char* path,uint32_t maxCores,uint64_t ubBytes){
    std::ifstream in(path);if(!in)throw std::runtime_error("plan 不可读");
    std::vector<Case> cases;std::string line;
    while(std::getline(in,line)){
        if(line.empty()||line[0]=='#')continue;
        std::replace(line.begin(),line.end(),',',' ');std::istringstream row(line);Case c{};
        if(!(row>>c.id>>c.direction>>c.shared>>c.cores>>c.tile>>c.batch>>c.requestedRing>>c.targetBytes>>c.control))
            throw std::runtime_error("plan 字段错误");
        if(c.direction!=1||c.shared!=0||!c.cores||c.cores>maxCores||
            !c.tile||c.tile%32||!c.batch||c.control>1||!c.requestedRing||
            c.requestedRing>(uint64_t(2)<<30)||c.targetBytes<c.requestedRing||
            2*uint64_t(c.tile)*c.batch+32+32768>ubBytes)throw std::runtime_error("非法边界");
        const uint64_t bank=c.tile*c.batch;
        c.perCoreRing=Ceil(c.shared?c.requestedRing:(c.requestedRing+c.cores-1)/c.cores,2*bank);
        c.actualRing=c.perCoreRing*(c.shared?1:c.cores);
        const uint64_t groups=std::max<uint64_t>(2,Ceil((c.targetBytes+c.cores*bank-1)/(c.cores*bank),2));
        if(groups>0xffffffffu)throw std::runtime_error("循环计数溢出");
        c.groups=groups;c.movedBytes=c.control?0:groups*c.cores*bank;
        if(!c.shared&&groups*bank<c.perCoreRing)throw std::runtime_error("未覆盖完整分区");
        c.outputBytes=c.direction?c.actualRing:2*bank*c.cores;
        cases.push_back(c);
    }
    if(cases.empty())throw std::runtime_error("空计划");return cases;
}
int main(int argc,char** argv){
    try{
        if(argc!=8)throw std::runtime_error("用法: DEVICE PLAN WARMUP SAMPLES OUTPUT UB_BYTES ORDER_SEED");
        Resources r{};r.device=std::stoi(argv[1]);
        int warmup=std::stoi(argv[3]),samples=std::stoi(argv[4]);
        if(warmup<0||samples<1)throw std::runtime_error("非法样本数");
        AC(aclInit(nullptr));r.initialized=true;
        AC(aclrtSetDevice(r.device));r.selected=true;
        int64_t maxCores=0,runtimeUb=0;
        AC(aclrtGetDeviceInfo(r.device,static_cast<aclrtDevAttr>(201),&maxCores));
        AC(aclrtGetDeviceInfo(r.device,static_cast<aclrtDevAttr>(204),&runtimeUb));
        const uint64_t ubBytes=runtimeUb>0?runtimeUb:std::stoull(argv[6]);
        const char* soc=aclrtGetSocName();
        if(!soc||std::string(soc).find("950")==std::string::npos||maxCores<=0)
            throw std::runtime_error("设备不是已支持的 Ascend950 AIV 环境");
        const auto cases=Plan(argv[2],maxCores,ubBytes);
        const uint32_t orderSeed=std::stoul(argv[7]);
        std::mt19937 rng(orderSeed);
        size_t inputBytes=32,outputBytes=32;
        for(const auto& c:cases){inputBytes=std::max<uint64_t>(inputBytes,c.actualRing);
            outputBytes=std::max<uint64_t>(outputBytes,c.outputBytes);}
        size_t freeBytes=0,totalBytes=0;AC(aclrtGetMemInfo(ACL_HBM_MEM,&freeBytes,&totalBytes));
        if(inputBytes+outputBytes+128+maxCores*32>freeBytes/2)
            throw std::runtime_error("可用 GM 不足，保持至少一半空闲容量");
        AC(aclrtCreateStream(&r.stream));
        AC(aclrtCreateEventWithFlag(&r.begin,ACL_EVENT_TIME_LINE));
        AC(aclrtCreateEventWithFlag(&r.end,ACL_EVENT_TIME_LINE));
        AC(aclrtMalloc(&r.x,inputBytes,ACL_MEM_MALLOC_NORMAL_ONLY));
        AC(aclrtMalloc(&r.out,outputBytes+128,ACL_MEM_MALLOC_NORMAL_ONLY));
        AC(aclrtMalloc(&r.ticks,maxCores*32,ACL_MEM_MALLOC_NORMAL_ONLY));
        std::vector<uint32_t> hostInput(inputBytes/4),actual(outputBytes/4);
        for(uint64_t i=0;i<hostInput.size();++i)hostInput[i]=InputValue(i);
        AC(aclrtMemcpy(r.x,inputBytes,hostInput.data(),inputBytes,ACL_MEMCPY_HOST_TO_DEVICE));
        std::ofstream out(argv[5]);if(!out)throw std::runtime_error("无法创建结果");
        out<<std::setprecision(17);
        for(const auto& c:cases){
            std::array<std::vector<double>,2> elapsed,warm,host;
            std::array<std::vector<std::vector<uint64_t>>,2> coreTicks;
            std::vector<uint32_t> orders;
            // 一对样本共用 GM 分配、参数和源 pattern，独立随机先测哪一实现。
            // 逐次重置目标并做完整 oracle；每模式的样本位置保持配对。

            const uint64_t bank=uint64_t(c.tile)*c.batch;
            for(int s=0;s<warmup+samples;++s){
                const uint32_t stamp=0x60000000u ^ (97*c.id+s+1);
                uint32_t first=rng()&1;orders.push_back(first);
                for(uint32_t pos=0;pos<2;++pos){
                const uint32_t mode=first^pos;
                AC(aclrtMemset(r.out,c.outputBytes+128,0xa5,c.outputBytes+128));
                AC(aclrtMemset(r.ticks,c.cores*32,0,c.cores*32));
                auto begin=std::chrono::steady_clock::now();
                AC(aclrtRecordEvent(r.begin,r.stream));
                launch_bandwidth(r.stream,r.x,r.out,r.ticks,c.cores,c.direction,c.shared,
                    c.tile,c.batch,c.perCoreRing,c.groups,c.control,stamp,mode);
                AC(aclrtRecordEvent(r.end,r.stream));
                AC(aclrtSynchronizeStream(r.stream));
                float ms=0;AC(aclrtEventElapsedTime(&ms,r.begin,r.end));
                if(!(ms>0))throw std::runtime_error("设备共同区间非正");
                double hostUs=std::chrono::duration<double,std::micro>(std::chrono::steady_clock::now()-begin).count();
                std::vector<uint64_t> tick(c.cores*4);
                AC(aclrtMemcpy(tick.data(),tick.size()*8,r.ticks,tick.size()*8,ACL_MEMCPY_DEVICE_TO_HOST));
                for(uint32_t core=0;core<c.cores;++core)
                    if(tick[core*4+1]<=tick[core*4]||tick[core*4+2]!=core||tick[core*4+3]!=0x414b4c42414e4431ULL)
                        throw std::runtime_error("每核计时未提交");
                // 全部写目标，或每核最后两组读出，逐 launch 做完整比较。
                AC(aclrtMemcpy(actual.data(),c.outputBytes,r.out,c.outputBytes,ACL_MEMCPY_DEVICE_TO_HOST));
                uint64_t bad=0;
                if(c.direction){
                    for(uint32_t core=0;core<c.cores;++core){
                        uint32_t expected=c.control?0xa5a5a5a5u:(0x9e3779b9u*(core+1))^stamp;
                        uint64_t offset=uint64_t(core)*c.perCoreRing/4;
                        for(uint64_t i=0;i<c.perCoreRing/4;++i)bad+=actual[offset+i]!=expected;
                    }
                }else{
                    for(uint32_t core=0;core<c.cores;++core)
                        for(uint32_t b=0;b<2;++b){
                            const uint64_t base=c.shared?0:uint64_t(core)*c.perCoreRing;
                            const uint64_t src=base+((uint64_t(c.groups-2+b)*bank)%c.perCoreRing);
                            const uint64_t offset=(uint64_t(core)*2+b)*bank/4;
                            for(uint64_t i=0;i<bank/4;++i)
                                bad+=actual[offset+i]!=(c.control?0:InputValue(src/4+i));
                        }
                }
                unsigned char guard[128];
                AC(aclrtMemcpy(guard,128,static_cast<unsigned char*>(r.out)+c.outputBytes,128,ACL_MEMCPY_DEVICE_TO_HOST));
                for(auto v:guard)bad+=v!=0xa5;
                if(bad)throw std::runtime_error("输出/保护区错误 case="+std::to_string(c.id)+" mismatches="+std::to_string(bad));
                if(s<warmup)warm[mode].push_back(ms);
                else{elapsed[mode].push_back(ms);host[mode].push_back(hostUs);coreTicks[mode].push_back(std::move(tick));}
                }
            }
            for(uint32_t mode=0;mode<2;++mode){
            out<<"{\"mode\":\""<<(mode?"tail":"windowed")<<"\",\"order_seed\":"<<orderSeed<<",\"id\":"<<c.id<<",\"direction\":"<<c.direction<<",\"shared_read\":"<<c.shared
                <<",\"cores\":"<<c.cores<<",\"tile_bytes\":"<<c.tile<<",\"batch\":"<<c.batch
                <<",\"requested_ring_bytes\":"<<c.requestedRing<<",\"actual_ring_bytes\":"<<c.actualRing
                <<",\"per_core_ring_bytes\":"<<c.perCoreRing<<",\"target_bytes\":"<<c.targetBytes
                <<",\"groups\":"<<c.groups<<",\"control\":"<<c.control<<",\"moved_bytes\":"<<c.movedBytes
                <<",\"soc\":\""<<soc<<"\",\"available_aiv\":"<<maxCores<<",\"ub_bytes\":"<<ubBytes
                <<",\"gm_total_bytes\":"<<totalBytes<<",\"gm_free_bytes_before\":"<<freeBytes
                <<",\"correctness\":true,\"validated_every_launch\":true,\"event_ms\":[";
            for(size_t i=0;i<elapsed[mode].size();++i)out<<(i?",":"")<<elapsed[mode][i];
            out<<"],\"warmup_event_ms\":[";for(size_t i=0;i<warm[mode].size();++i)out<<(i?",":"")<<warm[mode][i];
            out<<"],\"host_launch_sync_us\":[";for(size_t i=0;i<host[mode].size();++i)out<<(i?",":"")<<host[mode][i];
            out<<"],\"core_raw_ticks\":[";
            for(size_t i=0;i<coreTicks[mode].size();++i){
                out<<(i?",[":"[");
                for(size_t j=0;j<coreTicks[mode][i].size();++j)out<<(j?",\"":"\"")<<coreTicks[mode][i][j]<<'"';
                out<<']';
            }
            out<<"],\"pair_first_modes\":[";
            for(size_t i=0;i<orders.size();++i)out<<(i?",":"")<<orders[i];
            out<<"],\"launch_oracle\":[";
            for(size_t i=0;i<orders.size();++i)out<<(i?",":"")<<"{\"index\":"<<i<<",\"warmup\":"<<(i<size_t(warmup)?"true":"false")
                <<",\"checked_bytes\":"<<c.outputBytes+128<<",\"mismatches\":0}";
            out<<"]}\n";out.flush();
            std::cout<<"PASS case="<<c.id<<" cores="<<c.cores<<" tile="<<c.tile<<" batch="<<c.batch<<" mode="<<mode<<std::endl;
            }
        }
        return 0;
    }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
