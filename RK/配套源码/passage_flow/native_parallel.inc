#include <opencv2/core/parallel/parallel_backend.hpp>
#include <thread>
#include <mutex>
#include <condition_variable>
#include <atomic>
#include <exception>
#include <sstream>
#include <memory>
#include <sched.h>
#include <sys/syscall.h>
#include <sys/resource.h>
#include <sys/prctl.h>
#include <unistd.h>
#include <cerrno>
#include <ctime>
#ifndef FLOW_COLLECTOR_CPU
#define FLOW_COLLECTOR_CPU 6
#endif
#ifndef FLOW_WORKER_CPU
#define FLOW_WORKER_CPU 7
#endif

namespace {
thread_local bool inside_flow_pool = false;
long flow_tid() {
    return syscall(SYS_gettid);
}
long long flow_cpu_ns() {
    timespec t{};
    clock_gettime(CLOCK_THREAD_CPUTIME_ID, &t);
    return t.tv_sec * 1000000000ll + t.tv_nsec;
}
bool flow_on_cpu(long tid, int cpu) {
    cpu_set_t mask;
    CPU_ZERO(&mask);
    return !sched_getaffinity(tid, sizeof(mask), &mask) && CPU_COUNT(&mask) == 1 &&
           CPU_ISSET(cpu, &mask);
}
int flow_pin(long tid, int cpu) {
    cpu_set_t mask;
    CPU_ZERO(&mask);
    CPU_SET(cpu, &mask);
    return sched_setaffinity(tid, sizeof(mask), &mask);
}
std::string flow_cpus(long tid) {
    cpu_set_t mask;
    CPU_ZERO(&mask);
    if (tid <= 0 || sched_getaffinity(tid, sizeof(mask), &mask))
        return "[]";
    std::ostringstream s;
    s << '[';
    bool first = true;
    for (int i = 0; i < CPU_SETSIZE; ++i)
        if (CPU_ISSET(i, &mask)) {
            if (!first)
                s << ',';
            s << i;
            first = false;
        }
    s << ']';
    return s.str();
}

class PinnedFlowBackend final : public cv::parallel::ParallelForAPI {
    mutable std::mutex jobs_mutex, dispatch_mutex;
    std::condition_variable wake, finished;
    std::thread worker;
    bool stopping = false, job_ready = false, job_finished = true, worker_ready = false;
    FN_parallel_for_body_cb_t callback = nullptr;
    void *callback_data = nullptr;
    int job_start = 0, job_end = 0;
    std::exception_ptr worker_exception;

  public:
    std::atomic<int> threads{1}, worker_error{0};
    std::atomic<long> collector_tid{0}, worker_tid{0};
    std::atomic<unsigned long long> parallel_calls{0}, worker_tasks{0}, main_tasks{0};
    std::atomic<long long> worker_cpu_ns{0};
    ~PinnedFlowBackend() override {
        shutdown();
    }
    void shutdown() {
        {
            std::lock_guard<std::mutex> lock(jobs_mutex);
            stopping = true;
            wake.notify_one();
        }
        if (worker.joinable())
            worker.join();
        worker_tid = 0;
    }
    int start() {
        if (worker.joinable())
            return worker_error.load();
        {
            std::lock_guard<std::mutex> lock(jobs_mutex);
            stopping = false;
            worker_ready = false;
            job_ready = false;
            job_finished = true;
            worker_error = 0;
        }
        int inherited_nice = getpriority(PRIO_PROCESS, flow_tid());
        worker = std::thread([this, inherited_nice] {
            long tid = flow_tid();
            worker_tid = tid;
            prctl(PR_SET_NAME, "vision-lk-worker", 0, 0, 0);
            int error = flow_pin(tid, FLOW_WORKER_CPU) ? errno : 0;
            sched_param parameter{};
            if (!error && sched_setscheduler(tid, SCHED_OTHER, &parameter))
                error = errno;
            if (!error && setpriority(PRIO_PROCESS, tid, inherited_nice))
                error = errno;
            if (!error && !flow_on_cpu(tid, FLOW_WORKER_CPU))
                error = EINVAL;
            {
                std::lock_guard<std::mutex> lock(jobs_mutex);
                worker_error = error;
                worker_ready = true;
                finished.notify_all();
            }
            std::unique_lock<std::mutex> lock(jobs_mutex);
            while (true) {
                wake.wait(lock, [&] { return stopping || job_ready; });
                if (stopping)
                    return;
                auto function = callback;
                auto data = callback_data;
                int first = job_start, last = job_end;
                job_ready = false;
                lock.unlock();
                std::exception_ptr failure;
                try {
                    if (!flow_on_cpu(tid, FLOW_WORKER_CPU) && flow_pin(tid, FLOW_WORKER_CPU))
                        throw std::runtime_error("LK worker affinity changed");
                    inside_flow_pool = true;
                    function(first, last, data);
                    inside_flow_pool = false;
                } catch (...) {
                    inside_flow_pool = false;
                    failure = std::current_exception();
                }
                ++worker_tasks;
                worker_cpu_ns = flow_cpu_ns();
                lock.lock();
                worker_exception = failure;
                job_finished = true;
                finished.notify_all();
            }
        });
        std::unique_lock<std::mutex> lock(jobs_mutex);
        if (!finished.wait_for(lock, std::chrono::seconds(2), [&] { return worker_ready; }))
            return ETIMEDOUT;
        return worker_error.load();
    }
    void parallel_for(int tasks, FN_parallel_for_body_cb_t body, void *data) override {
        if (tasks < 2 || threads.load() != 2 || inside_flow_pool) {
            body(0, tasks, data);
            return;
        }
        std::unique_lock<std::mutex> dispatch(dispatch_mutex);
        if (worker_error.load())
            throw std::runtime_error("LK native worker unavailable");
        if (!flow_on_cpu(flow_tid(), FLOW_COLLECTOR_CPU))
            throw std::runtime_error("LK collector left configured CPU");
        int middle = tasks / 2;
        {
            std::lock_guard<std::mutex> lock(jobs_mutex);
            callback = body;
            callback_data = data;
            job_start = middle;
            job_end = tasks;
            worker_exception = nullptr;
            job_finished = false;
            job_ready = true;
        }
        wake.notify_one();
        std::exception_ptr main_exception;
        try {
            inside_flow_pool = true;
            body(0, middle, data);
            inside_flow_pool = false;
        } catch (...) {
            inside_flow_pool = false;
            main_exception = std::current_exception();
        }
        ++main_tasks;
        ++parallel_calls;
        std::unique_lock<std::mutex> lock(jobs_mutex);
        finished.wait(lock, [&] { return job_finished; });
        auto failure = worker_exception;
        lock.unlock();
        if (main_exception)
            std::rethrow_exception(main_exception);
        if (failure)
            std::rethrow_exception(failure);
    }
    int getThreadNum() const override {
        return flow_tid() == worker_tid.load() ? 1 : 0;
    }
    int getNumThreads() const override {
        return threads.load();
    }
    int setNumThreads(int count) override {
        int old = threads.load();
        threads = count == 2 ? 2 : 1;
        return old;
    }
    const char *getName() const override {
        return "VISION_PINNED_NATIVE_LK";
    }
};
std::shared_ptr<PinnedFlowBackend> pinned_flow_backend;
std::mutex flow_plan_mutex;
int prepare_flow_plan(int threads) {
    if (threads != 1 && threads != 2)
        return -1;
    std::lock_guard<std::mutex> guard(flow_plan_mutex);
    if (!pinned_flow_backend) {
        pinned_flow_backend = std::make_shared<PinnedFlowBackend>();
        cv::parallel::setParallelForBackend(pinned_flow_backend, false);
    }
    auto &plan = *pinned_flow_backend;
    if (threads == 1) {
        plan.threads = 1;
        plan.shutdown();
    }
    if (threads == 2) {
        if (flow_pin(flow_tid(), FLOW_COLLECTOR_CPU))
            return -10 - errno;
        if (!flow_on_cpu(flow_tid(), FLOW_COLLECTOR_CPU))
            return -11;
        int error = plan.start();
        if (error)
            return -100 - error;
    }
    plan.collector_tid = flow_tid();
    plan.threads = threads;
    cv::setNumThreads(threads);
    if (threads == 2) {
        cv::parallel_for_(cv::Range(0, 16), [](const cv::Range &range) {
            volatile unsigned total = 0;
            for (int i = range.start; i < range.end; ++i)
                for (int j = 0; j < 512; ++j)
                    total += static_cast<unsigned>(i + j);
            (void)total;
        });
        if (!plan.worker_tasks.load() || !flow_on_cpu(plan.worker_tid.load(), FLOW_WORKER_CPU))
            return -12;
    }
    return 0;
}
}
extern "C" {
int passage_flow_configure_threads(int threads) {
    try {
        return prepare_flow_plan(threads);
    } catch (...) {
        return -200;
    }
}
void passage_flow_shutdown_pool() {
    if (pinned_flow_backend) {
        pinned_flow_backend->threads = 1;
        pinned_flow_backend->shutdown();
    }
}
const char *passage_flow_thread_snapshot() {
    static thread_local std::string result;
    if (!pinned_flow_backend) {
        result = "{\"configured\":false}";
        return result.c_str();
    }
    auto &p = *pinned_flow_backend;
    std::ostringstream s;
    s << "{\"configured\":true,\"backend\":\"VISION_PINNED_NATIVE_LK\",\"threads\":"
      << p.threads.load() << ",\"collector_target_cpu\":" << FLOW_COLLECTOR_CPU
      << ",\"worker_target_cpu\":" << FLOW_WORKER_CPU << ",\"probe_cpu_build\":"
      << ((FLOW_COLLECTOR_CPU == 6 && FLOW_WORKER_CPU == 7) ? "false" : "true")
      << ",\"opencv_threads\":" << cv::getNumThreads()
      << ",\"collector_tid\":" << p.collector_tid.load()
      << ",\"collector_cpus\":" << flow_cpus(p.collector_tid.load())
      << ",\"worker_alive\":" << (p.worker_tid.load() > 0 ? "true" : "false")
      << ",\"worker_tid\":" << p.worker_tid.load()
      << ",\"worker_cpus\":" << flow_cpus(p.worker_tid.load())
      << ",\"worker_error\":" << p.worker_error.load()
      << ",\"parallel_calls\":" << p.parallel_calls.load()
      << ",\"worker_tasks\":" << p.worker_tasks.load() << ",\"main_tasks\":" << p.main_tasks.load()
      << ",\"worker_cpu_ns\":" << p.worker_cpu_ns.load() << '}';
    result = s.str();
    return result.c_str();
}
}
