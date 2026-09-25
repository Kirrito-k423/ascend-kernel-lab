#pragma once
#include "akl/datacopy_shape_protocol.h"
#include <acl/acl.h>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include <cstdlib>
#ifndef AKL_COPY_SOURCE_SHA256
#define AKL_COPY_SOURCE_SHA256 "unknown"
#endif
namespace akl::copy_shape {
inline std::string JsonString(const std::string& s) {
    std::string out="\"";
    const char* hex="0123456789abcdef";
    for (unsigned char c : s) {
        if (c=='"' || c=='\\') { out+='\\'; out+=c; }
        else if (c<32) { out+="\\u00"; out+=hex[c>>4]; out+=hex[c&15]; }
        else out+=c;
    }
    return out+'"';
}
inline std::string ToolkitVersion() {
    const char* home=std::getenv("ASCEND_HOME_PATH");
    if (!home || !*home) return "null";
    for (const char* arch : {"x86_64-linux", "aarch64-linux"}) {
        const auto p=std::filesystem::path(home)/arch/"ascend_toolkit_install.info";
        std::ifstream in(p);
        std::string line;
        while (std::getline(in,line)) {
            if (line.rfind("version=",0)==0) return JsonString(line.substr(8));
        }
    }
    return "null";
}
class Capture {
public:
    Capture(uint32_t blocks, void* stream): blocks_(blocks),stream_(stream) {
        bytes_=size_t(blocks)*kWords*8;
        if (!blocks || bytes_/(kWords*8)!=blocks) throw std::invalid_argument("copy shape capacity overflow");
        Check(aclrtMalloc(&data_,bytes_,ACL_MEM_MALLOC_HUGE_FIRST));
        const auto status=aclrtMemset(data_,bytes_,0,bytes_);
        if(status!=ACL_SUCCESS){aclrtFree(data_);data_=nullptr;Check(status);}
    }
    Capture(const Capture&)=delete;
    Capture& operator=(const Capture&)=delete;
    ~Capture(){if(data_ && aclrtSynchronizeStream(stream_)==ACL_SUCCESS) aclrtFree(data_);}
    uint8_t* Data()const{return static_cast<uint8_t*>(data_);}
    void Export(const std::filesystem::path& folder,uint32_t tokens,uint32_t h,uint32_t k,
                uint32_t elementBytes,uint32_t rank,uint32_t world,uint32_t firstBatch) {
        Check(aclrtSynchronizeStream(stream_));
        std::vector<uint64_t> rows(size_t(blocks_)*kWords);
        Check(aclrtMemcpy(rows.data(),bytes_,data_,bytes_,ACL_MEMCPY_DEVICE_TO_HOST));
        Write(folder/"datacopy-shapes.bin",reinterpret_cast<const char*>(rows.data()),bytes_);
        int major=0,minor=0,patch=0; const auto versionStatus=aclrtGetVersion(&major,&minor,&patch);
        const char* soc=aclrtGetSocName();
        const char* topology=std::getenv("AKL_MACHINE_LABEL");
        std::ostringstream meta;
        meta << "{\"schema\":\"akl.datacopy.capture.v1\",\"blocks\":"<<blocks_
             <<",\"slots\":"<<kSlots<<",\"record_words\":"<<kRecordWords
             <<",\"soc\":"<<JsonString(soc?soc:"unknown")
             <<",\"cann_version\":"<<ToolkitVersion()<<",\"acl_version\":";
        if(versionStatus==ACL_SUCCESS) meta<<JsonString(std::to_string(major)+"."+std::to_string(minor)+"."+std::to_string(patch));
        else meta<<"null";
        meta<<",\"topology_label\":"<<JsonString(topology?topology:"unknown")
            <<",\"topology_source\":\"AKL_MACHINE_LABEL user label\",\"clock_hz\":null"
            <<",\"source_sha256\":"<<JsonString(AKL_COPY_SOURCE_SHA256)
            <<",\"compiler\":"<<JsonString(__VERSION__)
            <<",\"rank\":"<<rank<<",\"world_size\":"<<world<<",\"tokens\":"<<tokens
            <<",\"h\":"<<h<<",\"k\":"<<k<<",\"x_element_bytes\":"<<elementBytes
            <<",\"first_batch_tokens\":"<<firstBatch
            <<",\"boundary\":\"shape_observation_only\",\"trace_binding\":\"same folder capture.json and per-block anchor_sequence\"}";
        const auto s=meta.str();Write(folder/"datacopy-shapes.json",s.data(),s.size());
    }
private:
    static void Check(aclError status){if(status!=ACL_SUCCESS)throw std::runtime_error("copy shape ACL error="+std::to_string(status));}
    static void Write(const std::filesystem::path& p,const char* data,size_t size){
        std::ofstream out;out.exceptions(std::ios::failbit|std::ios::badbit);out.open(p,std::ios::binary);out.write(data,size);out.close();
    }
    uint32_t blocks_;void* stream_;void* data_=nullptr;size_t bytes_=0;
};
}
