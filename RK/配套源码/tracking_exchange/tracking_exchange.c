#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <limits.h>
#include <math.h>
#include <poll.h>
#include <stdint.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

#define MAX_RESPONSE (32u * 1024u * 1024u)

static double monotonic_seconds(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0)
        return -1.;
    return now.tv_sec + now.tv_nsec * 1e-9;
}

static int wait_fd(int fd, short events, double deadline) {
    struct pollfd descriptor = {.fd = fd, .events = events};
    for (;;) {
        double now = monotonic_seconds();
        if (now < 0.)
            return -errno;
        double remaining = deadline - now;
        if (remaining <= 0.)
            return -ETIMEDOUT;
        double milliseconds = ceil(remaining * 1000.);
        int timeout = milliseconds > INT_MAX ? INT_MAX : (int)milliseconds;
        int ready = poll(&descriptor, 1, timeout);
        if (ready < 0) {
            if (errno == EINTR)
                continue;
            return -errno;
        }
        if (!ready)
            continue;
        if (descriptor.revents & POLLNVAL)
            return -EBADF;

        if (descriptor.revents & (events | POLLHUP | POLLERR))
            return 0;
    }
}

static int transfer(int fd, unsigned char *data, size_t size, int writing, double deadline) {
    size_t offset = 0;
    while (offset < size) {
        int status = wait_fd(fd, writing ? POLLOUT : POLLIN, deadline);
        if (status)
            return status;
        ssize_t count = writing ? write(fd, data + offset, size - offset)
                                : read(fd, data + offset, size - offset);
        if (count < 0) {
            if (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)
                continue;
            return -errno;
        }
        if (count == 0)
            return -EPIPE;
        offset += (size_t)count;
    }
    return 0;
}

int tracking_exchange(int write_fd, int read_fd, const void *request, size_t request_size,
                      void **response, size_t *capacity, size_t *response_size, double deadline) {
    if (write_fd < 0 || read_fd < 0 || !request || !response || !capacity || !response_size ||
        request_size < 8 || request_size > MAX_RESPONSE + 8u || !isfinite(deadline))
        return -EINVAL;
    *response_size = 0;
    int status = transfer(write_fd, (unsigned char *)request, request_size, 1, deadline);
    if (status)
        return status;
    unsigned char prefix[8];
    status = transfer(read_fd, prefix, sizeof(prefix), 0, deadline);
    if (status)
        return status;
    uint64_t size = 0;
    for (unsigned int index = 0; index < 8; ++index)
        size |= (uint64_t)prefix[index] << (8u * index);
    if (size > MAX_RESPONSE)
        return -EMSGSIZE;
    if (size > *capacity) {
        void *grown = realloc(*response, (size_t)size);
        if (!grown)
            return -ENOMEM;
        *response = grown;
        *capacity = (size_t)size;
    }
    status = transfer(read_fd, *response, (size_t)size, 0, deadline);
    if (status)
        return status;
    *response_size = (size_t)size;
    return 0;
}

void tracking_exchange_free(void *response) {
    free(response);
}
