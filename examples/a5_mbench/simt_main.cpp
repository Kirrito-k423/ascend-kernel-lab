#include <acl/acl.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

extern "C" void launch_arithmetic(void*, void*, void*, void*, void*, uint32_t, uint32_t,
                                 uint32_t, uint32_t, uint32_t, uint32_t);
static void Check(aclError rc, const char* action) {
    if (rc) throw std::runtime_error(std::string(action) + " ACL=" + std::to_string(rc));
}
#define ACL_CHECK(call) Check((call), #call)
struct Resources {
    int device = 0;
    bool initialized = false, selected = false;
    void *x = nullptr, *rhs = nullptr, *out = nullptr, *ticks = nullptr;
    aclrtStream stream = nullptr;
    void ReleaseBuffers() {
        for (void** p : {&x, &rhs, &out, &ticks}) if (*p) { aclrtFree(*p); *p = nullptr; }
    }
    ~Resources() {
        if (stream) aclrtSynchronizeStream(stream);
        ReleaseBuffers();
        if (stream) aclrtDestroyStream(stream);
        if (selected) aclrtResetDevice(device);
        if (initialized) aclFinalize();
    }
};
struct Case {
    uint32_t id, impl, op, n, threads, steps, calls;
};
static float Expected(float value, float rhs, const Case& c, uint32_t index) {
    if (c.impl == 3) return static_cast<float>(c.calls - 1 + index);
    if (c.impl == 4) return value;
    // volatile 限制 Host 的循环折叠，逐步 FP32 舍入。
    volatile float v = value;
    for (uint32_t j = 0; j < c.steps; ++j) {
        if (c.op == 0) v = v + rhs;
        else if (c.op == 1) v = v - rhs;
        else if (c.op == 2) v = v * rhs;
        else v = v / rhs;
    }
    return v;
}
static std::vector<Case> Plan(const char* path) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("无法读取 plan");
    std::vector<Case> cases;
    std::string line;
    while (std::getline(in, line)) {
        if (line.empty() || line[0] == '#') continue;
        std::replace(line.begin(), line.end(), ',', ' ');
        std::istringstream values(line);
        Case c;
        if (!(values >> c.id >> c.impl >> c.op >> c.n >> c.threads >> c.steps >> c.calls))
            throw std::runtime_error("plan 字段错误");
        const std::vector<uint32_t> threads{1,8,16,32,64,128,256,512,1024,2048};
        if (c.impl > 4 || c.op > 3 || c.n < 32 || c.n > 8192 || c.n % 32 || !c.steps ||
            c.steps > 1024 || !c.calls || c.calls > 256 ||
            std::find(threads.begin(), threads.end(), c.threads) == threads.end() ||
            (c.impl == 3 && c.n < c.threads)) throw std::runtime_error("plan 超出实现边界");
        cases.push_back(c);
    }
    if (cases.empty()) throw std::runtime_error("空 plan");
    return cases;
}
int main(int argc, char** argv) {
    try {
        if (argc != 6) throw std::runtime_error("用法: DEVICE PLAN.csv WARMUP SAMPLES OUTPUT.jsonl");
        Resources r;
        r.device = std::stoi(argv[1]);
        const int warmup = std::stoi(argv[3]), samples = std::stoi(argv[4]);
        if (r.device < 0 || warmup < 0 || samples < 1) throw std::runtime_error("采样参数非法");
        const auto cases = Plan(argv[2]);
        std::ofstream output(argv[5]);
        if (!output) throw std::runtime_error("无法写出结果");
        ACL_CHECK(aclInit(nullptr)); r.initialized = true;
        ACL_CHECK(aclrtSetDevice(r.device)); r.selected = true;
        const char* soc = aclrtGetSocName();
        if (!soc || std::string(soc).find("950") == std::string::npos)
            throw std::runtime_error("实验面向 Ascend950");
        ACL_CHECK(aclrtCreateStream(&r.stream));
        for (const auto& c : cases) {
            const size_t bytes = c.n * sizeof(float);
            std::vector<float> x(c.n), rhs(c.n), actual(c.n), expected(c.n);
            for (uint32_t i = 0; i < c.n; ++i) {
                x[i] = 0.4f + static_cast<float>(i % 113) * 0.002f;
                rhs[i] = c.op < 2 ? 0.0001f * static_cast<float>(1 + i % 7)
                                   : 1.0001f + 0.00002f * static_cast<float>(i % 7);
                expected[i] = Expected(x[i], rhs[i], c, i);
            }
            ACL_CHECK(aclrtMalloc(&r.x, bytes, ACL_MEM_MALLOC_NORMAL_ONLY));
            ACL_CHECK(aclrtMalloc(&r.rhs, bytes, ACL_MEM_MALLOC_NORMAL_ONLY));
            ACL_CHECK(aclrtMalloc(&r.out, bytes + 128, ACL_MEM_MALLOC_NORMAL_ONLY));
            ACL_CHECK(aclrtMalloc(&r.ticks, 32, ACL_MEM_MALLOC_NORMAL_ONLY));
            ACL_CHECK(aclrtMemcpy(r.x, bytes, x.data(), bytes, ACL_MEMCPY_HOST_TO_DEVICE));
            ACL_CHECK(aclrtMemcpy(r.rhs, bytes, rhs.data(), bytes, ACL_MEMCPY_HOST_TO_DEVICE));
            std::vector<uint64_t> raw, warm;
            std::vector<double> host;
            double maxError = 0;
            for (int sample = 0; sample < warmup + samples; ++sample) {
                uint64_t timer[4]{};
                ACL_CHECK(aclrtMemset(r.out, bytes + 128, 0xa5, bytes + 128));
                ACL_CHECK(aclrtMemset(r.ticks, 32, 0, 32));
                auto start = std::chrono::steady_clock::now();
                launch_arithmetic(r.stream, r.x, r.rhs, r.out, r.ticks, c.impl, c.op,
                                  c.n, c.threads, c.steps, c.calls);
                ACL_CHECK(aclrtSynchronizeStream(r.stream));
                double us = std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now()-start).count();
                ACL_CHECK(aclrtMemcpy(actual.data(), bytes, r.out, bytes, ACL_MEMCPY_DEVICE_TO_HOST));
                ACL_CHECK(aclrtMemcpy(timer, 32, r.ticks, 32, ACL_MEMCPY_DEVICE_TO_HOST));
                unsigned char guard[128];
                ACL_CHECK(aclrtMemcpy(guard, 128, static_cast<unsigned char*>(r.out) + bytes,
                                     128, ACL_MEMCPY_DEVICE_TO_HOST));
                for (auto value : guard) if (value != 0xa5) throw std::runtime_error("输出保护区损坏");
                if (timer[2] != 0x414b4c53494d5431ULL || timer[3] != c.n || timer[1] <= timer[0])
                    throw std::runtime_error("原始计时未提交或非正");
                for (uint32_t i = 0; i < c.n; ++i) {
                    // TouchVF 只写 threads 个元素，其他元素保留初始值。
                    float oracle = c.impl == 3 && i >= c.threads ? x[i] : expected[i];
                    double error = std::abs(static_cast<double>(actual[i])-oracle);
                    maxError = std::max(maxError, error);
                    if (!std::isfinite(actual[i]) || error > 0.00003 * std::max(1.0, std::abs(double(oracle))))
                        throw std::runtime_error("输出错误 case="+std::to_string(c.id)+" index="+std::to_string(i));
                }
                if (sample < warmup) warm.push_back(timer[1]-timer[0]);
                else { raw.push_back(timer[1]-timer[0]); host.push_back(us); }
            }
            output << "{\"id\":" << c.id << ",\"impl\":" << c.impl << ",\"op\":" << c.op
                << ",\"elements\":" << c.n << ",\"threads\":" << c.threads << ",\"steps\":" << c.steps
                << ",\"vf_calls\":" << c.calls << ",\"aiv_count\":1,\"dtype\":\"float32\",\"soc\":\""
                << soc << "\",\"correctness\":true,\"max_abs_error\":" << maxError << ",\"raw_ticks\":[";
            for (size_t i=0; i<raw.size(); ++i) output << (i ? ",\"" : "\"") << raw[i] << '"';
            output << "],\"warmup_ticks\":[";
            for (size_t i=0; i<warm.size(); ++i) output << (i ? ",\"" : "\"") << warm[i] << '"';
            output << "],\"host_launch_sync_us\":[";
            for (size_t i=0; i<host.size(); ++i) output << (i ? "," : "") << host[i];
            output << "]}\n"; output.flush();
            std::cout << "PASS case=" << c.id << " impl=" << c.impl << " n=" << c.n
                      << " threads=" << c.threads << " steps=" << c.steps << std::endl;
            r.ReleaseBuffers();
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
