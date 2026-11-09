/*
 * deadline-demo: a 1 kHz control loop and a small message server.
 *
 * Thread 2, priority 30: blocks in MsgReceivePulse() until a 1 ms timer pulse arrives, then does
 *   about 50 us of work. Its wake-up latency (pulse -> running) is what the "latency" budget checks.
 * Thread 3, priority 20: serves requests from deadline-client (MsgReceive, about 80 us of work,
 *   MsgReply). The client's MsgSend round trip is what the "roundtrip" budget checks.
 */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/dispatch.h>
#include <sys/siginfo.h>
#include "common.h"

#define PULSE_CODE_TICK (_PULSE_CODE_MINAVAIL + 1)
#define LOOP_PRIORITY 30
#define SERVER_PRIORITY 20

static void *control_loop(void *unused)
{
    (void)unused;
    int const chid = ChannelCreate(_NTO_CHF_PRIVATE);
    int const coid = ConnectAttach(0, 0, chid, _NTO_SIDE_CHANNEL, 0);
    if (chid == -1 || coid == -1) {
        perror("control loop channel");
        exit(EXIT_FAILURE);
    }
    struct sigevent ev;
    SIGEV_PULSE_INIT(&ev, coid, LOOP_PRIORITY, PULSE_CODE_TICK, 0);
    timer_t timer;
    if (timer_create(CLOCK_MONOTONIC, &ev, &timer) == -1) {
        perror("timer_create");
        exit(EXIT_FAILURE);
    }
    struct itimerspec const period = { .it_value = { 0, 1000000 }, .it_interval = { 0, 1000000 } };
    timer_settime(timer, 0, &period, NULL);
    for (;;) {
        struct _pulse pulse;
        if (MsgReceivePulse(chid, &pulse, sizeof pulse, NULL) == 0 && pulse.code == PULSE_CODE_TICK) {
            spin_us(50);
        }
    }
    return NULL;
}

static void *server(void *unused)
{
    (void)unused;
    name_attach_t *const attach = name_attach(NULL, SERVICE_NAME, 0);
    if (attach == NULL) {
        perror("name_attach");
        exit(EXIT_FAILURE);
    }
    for (;;) {
        union {
            struct _pulse pulse;
            uint32_t request;
        } msg;
        int const rcvid = MsgReceive(attach->chid, &msg, sizeof msg, NULL);
        if (rcvid == -1) {
            continue;
        }
        if (rcvid == 0) {
            continue; /* pulses: connect/disconnect notifications */
        }
        if (msg.pulse.type == _IO_CONNECT) {
            MsgReply(rcvid, EOK, NULL, 0); /* name_open() handshake */
            continue;
        }
        spin_us(80);
        uint32_t const reply = msg.request + 1;
        MsgReply(rcvid, EOK, &reply, sizeof reply);
    }
    return NULL;
}

int main(int argc, char **argv)
{
    /* A 1 ms timer on the default 1 ms clock tick fires on the tick after it expires, so the loop runs
     * at about 2 ms. A 100 us tick keeps it at 1 ms; pass --default-tick to see the bug. */
    if (argc < 2 || strcmp(argv[1], "--default-tick") != 0) {
        struct _clockperiod const fine = { .nsec = 100000, .fract = 0 };
        if (ClockPeriod(CLOCK_REALTIME, &fine, NULL, 0) == -1) {
            perror("ClockPeriod");
        }
    }
    pthread_t loop, srv;
    if (start_thread(&loop, LOOP_PRIORITY, control_loop, NULL) != 0 ||
        start_thread(&srv, SERVER_PRIORITY, server, NULL) != 0) {
        fprintf(stderr, "deadline-demo: cannot start threads (are you root?)\n");
        return EXIT_FAILURE;
    }
    pthread_join(loop, NULL);
    return EXIT_SUCCESS;
}
