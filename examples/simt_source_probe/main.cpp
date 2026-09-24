#include <acl/acl.h>
#include <charconv>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include <unistd.h>

extern "C" void launch_probe(void*, void*, void*, uint32_t, uint32_t);
static void Check(aclError code, const char* operation) {
    if (code) throw std::runtime_error(std::string(operation) + ": ACL=" + std::to_string(code));
}
#define ACL_CHECK(call) Check((call), #call)
static uint32_t Number(const char* text, uint32_t maximum) {
    uint32_t value = 0;
    const char* end = text + std::strlen(text);
    auto result = std::from_chars(text, end, value);
    if (result.ec != std::errc{} || result.ptr != end || value > maximum)
        throw std::runtime_error("非法数值参数");
    return value;
}
struct Resources {
    int device;
    bool initialized = false, selected = false;
    void* input = nullptr;
    void* output = nullptr;
    aclrtStream stream = nullptr;
    ~Resources() {
        if (stream) aclrtSynchronizeStream(stream);
        if (output) aclrtFree(output);
        if (input) aclrtFree(input);
        if (stream) aclrtDestroyStream(stream);
        if (selected) aclrtResetDevice(device);
        if (initialized) aclFinalize();
    }
};
int main(int argc, char** argv) {
    try {
        if (argc != 3 && argc != 5)
            throw std::runtime_error("用法: akl_simt_probe DEVICE STEPS(1..1048576) [LAUNCHES(1..2) STATEFUL(0|1)]");
        Resources r{static_cast<int>(Number(argv[1], 2147483647U))};
        const uint32_t steps = Number(argv[2], 1048576U);
        const uint32_t launches = argc == 5 ? Number(argv[3], 2U) : 1U;
        const uint32_t stateful = argc == 5 ? Number(argv[4], 1U) : 0U;
        if (!steps || !launches) throw std::runtime_error("steps 和 launches 必须大于 0");
        if (stateful) std::cout << "AKL_APP_START pid=" << getpid() << std::endl;
        ACL_CHECK(aclInit(nullptr));
        r.initialized = true;
        ACL_CHECK(aclrtSetDevice(r.device));
        r.selected = true;
        const char* soc = aclrtGetSocName();
        if (!soc || std::string(soc).find("950") == std::string::npos)
            throw std::runtime_error("该探针只面向 Ascend 950");
        ACL_CHECK(aclrtCreateStream(&r.stream));
        std::vector<uint32_t> table(4096 + (stateful ? 32 : 0)), output(32, 0xffffffffU);
        for (uint32_t i = 0; i < 4096; ++i) table[i] = (i + 131U) % 4096U;
        const size_t bytes = table.size() * sizeof(uint32_t);
        const size_t outBytes = output.size() * sizeof(uint32_t);
        ACL_CHECK(aclrtMalloc(&r.input, bytes, ACL_MEM_MALLOC_NORMAL_ONLY));
        ACL_CHECK(aclrtMalloc(&r.output, outBytes, ACL_MEM_MALLOC_NORMAL_ONLY));
        ACL_CHECK(aclrtMemcpy(r.input, bytes, table.data(), bytes, ACL_MEMCPY_HOST_TO_DEVICE));
        ACL_CHECK(aclrtMemcpy(r.output, outBytes, output.data(), outBytes, ACL_MEMCPY_HOST_TO_DEVICE));
        for (uint32_t launch = 0; launch < launches; ++launch)
            launch_probe(r.stream, r.input, r.output, steps, stateful);
        ACL_CHECK(aclrtSynchronizeStream(r.stream));
        ACL_CHECK(aclrtMemcpy(output.data(), outBytes, r.output, outBytes, ACL_MEMCPY_DEVICE_TO_HOST));
        if (stateful) {
            uint32_t counts[32];
            ACL_CHECK(aclrtMemcpy(counts, sizeof(counts), static_cast<uint32_t*>(r.input) + 4096,
                                 sizeof(counts), ACL_MEMCPY_DEVICE_TO_HOST));
            // 恢复快照可能掩盖额外启动；这些值不是 profiler 的真实调用次数。
            std::cout << "AKL_STATE {\"pid\":" << getpid() << ",\"expected\":" << launches << ",\"counts\":[";
            for (uint32_t lane = 0; lane < 32; ++lane)
                std::cout << (lane ? "," : "") << counts[lane];
            std::cout << "]}" << std::endl;
            for (auto count : counts)
                if (count != launches) throw std::runtime_error("应用可见 GM 状态与显式启动次数不匹配");
        }
        for (uint32_t lane = 0; lane < output.size(); ++lane) {
            uint32_t pos = lane, expected = lane;
            for (uint32_t i = 0; i < steps; ++i) {
                pos = table[pos];
                expected = static_cast<uint32_t>(uint64_t(expected) * 1664525U + pos + 1013904223U);
            }
            if (output[lane] != expected)
                throw std::runtime_error("结果不匹配: lane=" + std::to_string(lane));
        }
        std::cout << "PASS soc=" << soc << " blocks=1 threads=32 table_words=4096 steps="
                  << steps << " checked=32\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
