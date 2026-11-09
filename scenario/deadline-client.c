/* deadline-client: sends a request to deadline-demo every 5 ms (MsgSend) from thread 1, priority 25. */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <sys/dispatch.h>
#include "common.h"

int main(void)
{
    struct sched_param sp = { .sched_priority = 25 };
    pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp);
    int coid = -1;
    for (int tries = 0; tries < 50 && coid == -1; tries++) {
        coid = name_open(SERVICE_NAME, 0);
        if (coid == -1) {
            struct timespec const wait = { 0, 100000000 };
            nanosleep(&wait, NULL);
        }
    }
    if (coid == -1) {
        perror("name_open " SERVICE_NAME);
        return EXIT_FAILURE;
    }
    struct timespec const gap = { 0, 5000000 };
    for (uint32_t n = 0;; n++) {
        uint32_t reply = 0;
        if (MsgSend(coid, &n, sizeof n, &reply, sizeof reply) == -1) {
            perror("MsgSend");
            return EXIT_FAILURE;
        }
        nanosleep(&gap, NULL);
    }
}
