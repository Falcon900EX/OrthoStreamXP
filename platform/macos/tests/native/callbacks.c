/* SPDX-License-Identifier: GPL-3.0-only
 * Test-only helper: callbacks originate on a pthread, not a Python thread.
 */
#include <pthread.h>
#include <stddef.h>
typedef int (*callback_t)(int);
struct job { callback_t callback; int count; int failed_at; };
static void *work(void *value) {
    struct job *j = value;
    for (int i = 0; i < j->count; ++i) {
        if (j->callback(i) != 0) { j->failed_at = i; break; }
    }
    return NULL;
}
int run_native_callbacks(callback_t callback, int count) {
    pthread_t thread;
    struct job j = {callback, count, -1};
    if (pthread_create(&thread, NULL, work, &j) != 0) return -2;
    if (pthread_join(thread, NULL) != 0) return -3;
    return j.failed_at;
}
