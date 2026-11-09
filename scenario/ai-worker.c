/*
 * ai-worker: a stand-in for a best-effort inference job. Every 10 ms it computes for duty% of the
 * period, then sleeps. Run it below the control loop (e.g. priority 10) and nothing happens; run it
 * above (e.g. priority 40) with a high duty cycle and the control loop misses its deadlines.
 *
 *   ai-worker [-p priority] [-d duty_percent]
 */
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
#include "common.h"

int main(int argc, char **argv)
{
    int priority = 10, duty = 30, opt;
    while ((opt = getopt(argc, argv, "p:d:")) != -1) {
        if (opt == 'p') {
            priority = atoi(optarg);
        } else if (opt == 'd') {
            duty = atoi(optarg);
        } else {
            fprintf(stderr, "usage: ai-worker [-p priority] [-d duty_percent]\n");
            return EXIT_FAILURE;
        }
    }
    if (duty < 0 || duty > 100) {
        duty = 30;
    }
    struct sched_param sp = { .sched_priority = priority };
    pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp);
    unsigned const period_us = 10000, busy_us = period_us * (unsigned)duty / 100u;
    struct timespec const rest = { 0, (long)(period_us - busy_us) * 1000L };
    for (;;) {
        spin_us(busy_us);
        if (rest.tv_nsec > 0) {
            nanosleep(&rest, NULL);
        }
    }
}
