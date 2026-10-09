#include <acl/acl.h>
#include "shmem.h"
#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>
#include <algorithm>
#include <chrono>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <vector>
extern "C" void launch_network(void*,void*,void*,void*,uint32_t,uint32_t,uint32_t,
    uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t);
static void Check(int rc,const char* op){if(rc)throw std::runtime_error(std::string(op)+" rc="+std::to_string(rc));}
#define AC(x) Check((x),#x)
struct Coord {
    int fd=-1;uint64_t sequence=0;
    ~Coord(){if(fd>=0)close(fd);}
    void Open(int rank,const std::string& endpoint){
        if(endpoint.rfind("tcp://",0)!=0)throw std::runtime_error("bootstrap 必须为 tcp://IPv4:PORT");
        auto sep=endpoint.rfind(':');auto host=endpoint.substr(6,sep-6);int port=std::stoi(endpoint.substr(sep+1));
        if(port<1024||port>=65535)throw std::runtime_error("端口范围错误");
        sockaddr_in addr{};addr.sin_family=AF_INET;addr.sin_port=htons(port+1);
        if(inet_pton(AF_INET,host.c_str(),&addr.sin_addr)!=1)throw std::runtime_error("需要 IPv4 地址");
        if(rank==0){
            int listener=socket(AF_INET,SOCK_STREAM,0);if(listener<0)throw std::runtime_error("socket 失败");
            timeval tv{90,0};setsockopt(listener,SOL_SOCKET,SO_RCVTIMEO,&tv,sizeof(tv));
            addr.sin_addr.s_addr=htonl(INADDR_ANY);
            if(bind(listener,reinterpret_cast<sockaddr*>(&addr),sizeof(addr))||listen(listener,1)){
                close(listener);throw std::runtime_error("协调端口被占用或无法监听");}
            fd=accept(listener,nullptr,nullptr);close(listener);
        }else{
            for(int i=0;i<90;++i){fd=socket(AF_INET,SOCK_STREAM,0);
                if(fd>=0&&connect(fd,reinterpret_cast<sockaddr*>(&addr),sizeof(addr))==0)break;
                if(fd>=0)close(fd);fd=-1;std::this_thread::sleep_for(std::chrono::seconds(1));}
        }
        if(fd<0)throw std::runtime_error("协调连接失败");
        timeval tv{90,0};setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&tv,sizeof(tv));
        setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&tv,sizeof(tv));
    }
    void Exchange(bool bad=false){
        uint64_t local[2]={++sequence,uint64_t(bad)},remote[2]{};
        size_t cursor=0;
        while(cursor<sizeof(local)){auto n=send(fd,reinterpret_cast<char*>(local)+cursor,sizeof(local)-cursor,MSG_NOSIGNAL);
            if(n<=0)throw std::runtime_error("协调发送失败");cursor+=n;}
        cursor=0;
        while(cursor<sizeof(remote)){auto n=recv(fd,reinterpret_cast<char*>(remote)+cursor,sizeof(remote)-cursor,0);
            if(n<=0)throw std::runtime_error("协调接收失败/对端退出");cursor+=n;}
        if(remote[0]!=sequence||local[1]||remote[1])throw std::runtime_error("双端验收失败/协调序号不一致");
    }
};
struct Case {
    uint32_t id,get,cores,batch,qps,control,part,slots,ops;
    uint64_t requested,workset,target,actual,ring,moved;
};
static uint64_t Ceil(uint64_t n,uint64_t unit){return (n+unit-1)/unit*unit;}
static std::vector<Case> Plan(const char* path,uint32_t maxCores,int engine,uint32_t& maxQps){
    std::ifstream in(path);if(!in)throw std::runtime_error("计划不可读");std::vector<Case> result;std::string line;
    while(std::getline(in,line)){
        if(line.empty()||line[0]=='#')continue;std::replace(line.begin(),line.end(),',',' ');
        std::istringstream row(line);Case c{};
        if(!(row>>c.id>>c.get>>c.cores>>c.requested>>c.batch>>c.qps>>c.workset>>c.target>>c.control))
            throw std::runtime_error("计划字段错误");
        if(c.get>1||!c.cores||c.cores>maxCores||!c.requested||c.requested>(64ull<<20)||
            !c.batch||c.batch>128||!c.qps||c.qps>8||c.control>1||c.workset>(64ull<<20)||
            !c.target||c.target>(1ull<<32)||(engine==1&&c.cores!=1)||
            (engine==0&&(c.qps!=1||c.batch!=1)))throw std::runtime_error("不支持的配置边界");
        c.part=Ceil((c.requested+c.cores-1)/c.cores,32);c.actual=uint64_t(c.part)*c.cores;
        c.slots=std::max<uint64_t>(c.batch,Ceil(std::max<uint64_t>(c.workset,c.actual),c.actual)/c.actual);
        c.ring=c.actual*c.slots;
        if(c.ring>(128ull<<20))throw std::runtime_error("ring 超过容量");
        const uint64_t ops=Ceil(std::max<uint64_t>(c.slots,(c.target+c.actual-1)/c.actual),c.batch);
        if(ops>0xffffffffu)throw std::runtime_error("循环数溢出");c.ops=ops;
        c.moved=c.control?0:ops*c.actual;maxQps=std::max(maxQps,c.qps);result.push_back(c);
    }
    if(result.empty())throw std::runtime_error("空计划");return result;
}
static uint32_t Pattern(int rank,uint64_t index,uint32_t stamp){
    return 0x13579bdfu ^ (2654435761u*uint32_t(index)) ^ (0x9e3779b9u*uint32_t(rank+1)) ^ stamp;
}
int main(int argc,char** argv){
    try{
        if(argc!=9)throw std::runtime_error("用法: DEVICE RANK tcp://IP:PORT mte|urma PLAN WARMUP SAMPLES OUTPUT");
        const int device=std::stoi(argv[1]),rank=std::stoi(argv[2]);
        const int engine=std::string(argv[4])=="mte"?0:std::string(argv[4])=="urma"?1:-1;
        const int warmup=std::stoi(argv[6]),samples=std::stoi(argv[7]);
        if(rank<0||rank>1||engine<0||warmup<0||samples<1)throw std::runtime_error("参数错误");
        AC(aclInit(nullptr));AC(aclrtSetDevice(device));
        int64_t maxCores=0;AC(aclrtGetDeviceInfo(device,static_cast<aclrtDevAttr>(201),&maxCores));
        const char* soc=aclrtGetSocName();
        if(!soc||std::string(soc).find("950")==std::string::npos||maxCores<=0)throw std::runtime_error("仅已适配 Ascend950");
        uint32_t maxQps=1;auto cases=Plan(argv[5],maxCores,engine,maxQps);uint64_t maxRing=0;
        for(auto c:cases)maxRing=std::max(maxRing,c.ring);
        size_t freeBytes=0,totalBytes=0;AC(aclrtGetMemInfo(ACL_HBM_MEM,&freeBytes,&totalBytes));
        // 使用 SDK 的页粒度对齐对称堆，避免将固定 1MiB 当成所有后端的合法粒度。
        const uint64_t heap=Ceil(2*(maxRing+256)+(32ull<<20),ACLSHMEM_PAGE_SIZE);
        if(heap>freeBytes/2)throw std::runtime_error("GM 空闲不足");
        Coord coord;coord.Open(rank,argv[3]);
        auto transport=engine==0?ACLSHMEM_DATA_OP_MTE:ACLSHMEM_DATA_OP_UDMA;
        if(engine==1)AC(aclshmemx_set_qp_num(transport,maxQps));
        aclshmemx_init_attr_t attr{};attr.my_pe=rank;attr.n_pes=2;attr.local_mem_size=heap;
        if(std::strlen(argv[3])>=sizeof(attr.ip_port))throw std::runtime_error("bootstrap 地址过长");
        std::strcpy(attr.ip_port,argv[3]);attr.option_attr.data_op_engine_type=transport;
        attr.option_attr.shm_init_timeout=90;attr.option_attr.shm_create_timeout=90;
        attr.option_attr.control_operation_timeout=90;
        AC(aclshmemx_init_attr(ACLSHMEMX_INIT_WITH_DEFAULT,&attr));
        auto x=static_cast<uint8_t*>(aclshmem_malloc(maxRing+256));
        auto y=static_cast<uint8_t*>(aclshmem_malloc(maxRing+256));
        if(!x||!y)throw std::runtime_error("SHMEM 分配失败");
        void* tickPtr=nullptr;AC(aclrtMalloc(&tickPtr,maxCores*32,ACL_MEM_MALLOC_NORMAL_ONLY));
        aclrtStream stream=nullptr;aclrtEvent begin=nullptr,end=nullptr;
        AC(aclrtCreateStream(&stream));AC(aclrtCreateEventWithFlag(&begin,ACL_EVENT_TIME_LINE));
        AC(aclrtCreateEventWithFlag(&end,ACL_EVENT_TIME_LINE));
        std::ofstream out(argv[8]);if(!out)throw std::runtime_error("结果不可写");out<<std::setprecision(17);
        for(const auto& c:cases){
            std::vector<uint32_t> source(c.ring/4+64),actual(source.size());
            for(int sample=0;sample<warmup+samples;++sample){
                const uint32_t stamp=(c.id*131+sample+1)^0x60000000u;
                std::fill(source.begin(),source.end(),0xa5a5a5a5u);
                for(uint64_t i=0;i<c.ring/4;++i)source[i+32]=Pattern(rank,i,stamp);
                AC(aclrtMemcpy(x,c.ring+256,source.data(),c.ring+256,ACL_MEMCPY_HOST_TO_DEVICE));
                AC(aclrtMemset(y,c.ring+256,0xa5,c.ring+256));
                AC(aclrtMemset(tickPtr,c.cores*32,0,c.cores*32));coord.Exchange();
                float ms=0;double hostUs=0;std::vector<uint64_t> ticks(c.cores*4);
                if(rank==0){
                    auto t=std::chrono::steady_clock::now();AC(aclrtRecordEvent(begin,stream));
                    launch_network(stream,x+128,y+128,tickPtr,c.cores,engine,c.get,c.part,c.slots,c.ops,c.batch,c.qps,c.control);
                    AC(aclrtRecordEvent(end,stream));AC(aclrtSynchronizeStream(stream));
                    AC(aclrtEventElapsedTime(&ms,begin,end));
                    hostUs=std::chrono::duration<double,std::micro>(std::chrono::steady_clock::now()-t).count();
                    if(!(ms>0))throw std::runtime_error("非正计时");
                    AC(aclrtMemcpy(ticks.data(),c.cores*32,tickPtr,c.cores*32,ACL_MEMCPY_DEVICE_TO_HOST));
                    for(uint32_t k=0;k<c.cores;++k)if(ticks[k*4+1]<=ticks[k*4]||ticks[k*4+2]!=k||
                        ticks[k*4+3]!=0x414b4c4e455431ULL)throw std::runtime_error("核计时不完整");
                }
                // TCP 仅用于测量窗口之外的准备/完成/验收协调，不参与带宽计时。
                coord.Exchange();uint64_t bad=0;
                AC(aclrtMemcpy(actual.data(),c.ring+256,x,c.ring+256,ACL_MEMCPY_DEVICE_TO_HOST));
                for(size_t i=0;i<actual.size();++i)bad+=actual[i]!=source[i];
                AC(aclrtMemcpy(actual.data(),c.ring+256,y,c.ring+256,ACL_MEMCPY_DEVICE_TO_HOST));
                const bool receive=!c.control&&rank==(c.get?0:1);const int sourceRank=c.get?1:0;
                for(size_t i=0;i<actual.size();++i){
                    const uint32_t expected=receive&&i>=32&&i<c.ring/4+32?Pattern(sourceRank,i-32,stamp):0xa5a5a5a5u;
                    bad+=actual[i]!=expected;
                }
                out<<"{\"case_id\":"<<c.id<<",\"rank\":"<<rank<<",\"sample\":"<<sample-warmup
                   <<",\"engine\":\""<<(engine?"urma":"mte")<<"\",\"operation\":\""<<(c.get?"get":"put")
                   <<"\",\"requested_bytes\":"<<c.requested<<",\"message_bytes\":"<<c.actual
                   <<",\"cores\":"<<c.cores<<",\"batch\":"<<c.batch<<",\"qps\":"<<c.qps
                   <<",\"configured_qps\":"<<maxQps<<",\"ring_bytes\":"<<c.ring<<",\"slots\":"<<c.slots
                   <<",\"operations\":"<<c.ops<<",\"moved_bytes\":"<<c.moved<<",\"control\":"<<c.control
                   <<",\"event_ms\":"<<ms<<",\"host_launch_sync_us\":"<<hostUs<<",\"soc\":\""<<soc
                   <<"\",\"available_aiv\":"<<maxCores<<",\"mismatches\":"<<bad<<",\"ticks\":[";
                if(rank==0)for(size_t i=0;i<ticks.size();++i)out<<(i?",\"":"\"")<<ticks[i]<<'"';
                out<<"]}\n";out.flush();coord.Exchange(bad!=0);
                std::cout<<"PASS "<<c.id<<' '<<sample<<std::endl;
            }
        }
        coord.Exchange();aclshmem_free(y);aclshmem_free(x);aclshmem_finalize();
        AC(aclrtFree(tickPtr));AC(aclrtDestroyEvent(begin));AC(aclrtDestroyEvent(end));
        AC(aclrtDestroyStream(stream));AC(aclrtResetDevice(device));AC(aclFinalize());return 0;
    }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
