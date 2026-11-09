/* Shared helpers for the Deadline CI demo scenario (QNX OS 8.0). */
#ifndef DEADLINE_COMMON_H
#define DEADLINE_COMMON_H
#include <stdint.h>
#include <pthread.h>
#include <sched.h>
#include <sys/neutrino.h>
#include <sys/syspage.h>

#define SERVICE_NAME "deadline-demo"

/* Busy work for about `us` microseconds, measured with the CPU cycle counter. */
static inline void spin_us(unsigned us)
{
    uint64_t const cps = SYSPAGE_ENTRY(qtime)->cycles_per_sec;
    uint64_t const end = ClockCycles() + cps * us / 1000000u;
    while (ClockCycles() < end) {
    }
}

/* Start a SCHED_FIFO thread at a fixed priority. */
static inline int start_thread(pthread_t *t, int priority, void *(*fn)(void *), void *arg)
{
    pthread_attr_t attr;
    struct sched_param sp = { .sched_priority = priority };
    pthread_attr_init(&attr);
    pthread_attr_setinheritsched(&attr, PTHREAD_EXPLICIT_SCHED);
    pthread_attr_setschedpolicy(&attr, SCHED_FIFO);
    pthread_attr_setschedparam(&attr, &sp);
    int const rc = pthread_create(t, &attr, fn, arg);
    pthread_attr_destroy(&attr);
    return rc;
}
#endif
