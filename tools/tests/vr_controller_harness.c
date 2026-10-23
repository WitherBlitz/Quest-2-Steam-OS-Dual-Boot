/* Host-only caller for actual upstream/patched controller functions.
 * This replaces only the external fusion endpoint; it estimates no pose.
 */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define G 9.80665f
#define DEG2RAD 0.017453292519943295f
struct xrt_device { unsigned unused; };
struct xrt_vec2 { float x, y; };
struct xrt_vec3 { float x, y, z; };
struct m_imu_3dof { unsigned calls; int64_t last, previous; };
struct q2_system;
/* CONTROLLER */
struct q2_system { struct q2_controller *ctrl[2]; };

static void
m_imu_3dof_update(struct m_imu_3dof *fusion, int64_t timestamp,
                  const struct xrt_vec3 *accel, const struct xrt_vec3 *gyro)
{
    (void)accel;
    (void)gyro;
    fusion->calls++;
    fusion->previous = fusion->last;
    fusion->last = timestamp;
}

/* FUNCTIONS */

#define REQUIRE(condition) do { if (!(condition)) { \
    fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, #condition); return 1; \
} } while (0)

static void
packet(struct q2_system *sys, unsigned hand, uint8_t key, const void *data,
       unsigned bytes, int64_t now)
{
    uint8_t raw[64] = {0};
    raw[10] = (uint8_t)hand;
    raw[24] = key;
    memcpy(raw + 25, data, bytes);
    handle_controller(sys, raw, (uint8_t)(25 + bytes), now);
}

static void
motion(struct q2_system *sys, unsigned hand, uint32_t device_us, int64_t now)
{
    uint8_t body[19] = {0};
    memcpy(body + 1, &device_us, 4);
    packet(sys, hand, 0x41, body, sizeof(body), now);
}

static int
test_case(const char *name)
{
    struct q2_controller left = {0}, right = {0};
    struct q2_system sys = {.ctrl = {&left, &right}};
    left.left = true;
    int64_t now = INT64_C(10000000000000);
    if (!strcmp(name, "wrap")) {
        motion(&sys, 0, UINT32_C(0xfffffff0), now);
        motion(&sys, 0, UINT32_C(0x000007c0), now + 2000000);
        REQUIRE(left.fusion.calls == 2);
        REQUIRE(left.fusion.last - left.fusion.previous >= 2000000);
        REQUIRE(left.fusion.last - left.fusion.previous <= 2001000);
    } else if (!strcmp(name, "implicit-wrap")) {
        uint8_t body[13] = {0};
        motion(&sys, 0, UINT32_C(0xfffffff0), now);
        packet(&sys, 0, 0x81, body, sizeof(body), now + 2000000);
        REQUIRE(left.fusion.calls == 2);
        REQUIRE(left.fusion.last - left.fusion.previous >= 2000000);
        REQUIRE(left.fusion.last - left.fusion.previous <= 2001000);
    } else if (!strcmp(name, "missing-epoch")) {
        uint8_t body[13] = {0};
        packet(&sys, 0, 0x81, body, sizeof(body), now);
        REQUIRE(left.fusion.calls == 0);
        motion(&sys, 0, 0, now);
        REQUIRE(left.fusion.calls == 1);
    } else if (!strcmp(name, "zero-epoch")) {
        motion(&sys, 0, 0, now);
        motion(&sys, 0, 2000, now + 2000000);
        REQUIRE(left.fusion.calls == 2);
        REQUIRE(left.fusion.last - left.fusion.previous >= 2000000);
    } else if (!strcmp(name, "duplicate")) {
        motion(&sys, 0, 10000, now);
        motion(&sys, 0, 10000, now + 2000000);
        REQUIRE(left.fusion.calls == 1);
        motion(&sys, 0, 12000, now + 2000000);
        REQUIRE(left.fusion.calls == 2);
    } else if (!strcmp(name, "backwards")) {
        motion(&sys, 0, 10000, now);
        motion(&sys, 0, 9000, now + 2000000);
        REQUIRE(left.fusion.calls == 1);
        REQUIRE(left.last_dev_us == 10000);
        motion(&sys, 0, 12000, now + 2000000);
        REQUIRE(left.fusion.calls == 2);
    } else if (!strcmp(name, "ambiguous-gap")) {
        motion(&sys, 0, 0, now);
        motion(&sys, 0, UINT32_C(0x80000000), now + 2000000);
        REQUIRE(left.fusion.calls == 1);
        REQUIRE(left.last_dev_us == 0);
    } else if (!strcmp(name, "large-reset")) {
        motion(&sys, 0, 1000000, now);
        motion(&sys, 0, 0, now + 2000000);
        REQUIRE(left.fusion.calls == 1);
        REQUIRE(left.last_dev_us == 1000000);
    } else if (!strcmp(name, "independent-clocks")) {
        motion(&sys, 0, UINT32_C(0xfffffff0), now);
        motion(&sys, 1, 0, now);
        motion(&sys, 0, UINT32_C(0x7c0), now + 2000000);
        motion(&sys, 1, 2000, now + 2000000);
        REQUIRE(left.fusion.calls == 2 && right.fusion.calls == 2);
        REQUIRE(left.fusion.last - left.fusion.previous >= 2000000);
        REQUIRE(right.fusion.last - right.fusion.previous >= 2000000);
    } else if (!strcmp(name, "overflow")) {
        uint32_t raw = 0;
        int16_t v[6] = {0};
        handle_ctrl_motion(&left, raw, v, now);
        /* floor((INT64_MAX / 1000) / 2147483647) = 4294967. */
        for (unsigned i = 0; i < 4294969; i++) {
            raw += UINT32_C(2147483647);
            handle_ctrl_motion(&left, raw, v, now + i + 1);
        }
        REQUIRE(left.fusion.calls == 4294968);
    } else if (!strcmp(name, "stick-range")) {
        for (int value = INT16_MIN; value <= INT16_MAX; value++) {
            int16_t axis = (int16_t)value;
            uint8_t body[5] = {0};
            memcpy(body + 1, &axis, 2);
            memcpy(body + 3, &axis, 2);
            packet(&sys, 0, 0x82, body, sizeof(body), now);
            REQUIRE(left.stick.x >= -1.0f && left.stick.x <= 1.0f);
            REQUIRE(left.stick.y >= -1.0f && left.stick.y <= 1.0f);
            if (value == INT16_MIN) REQUIRE(left.stick.x == -1.0f && left.stick.y == -1.0f);
            if (value == 0) REQUIRE(left.stick.x == 0.0f && left.stick.y == 0.0f);
            if (value == INT16_MAX) REQUIRE(left.stick.x == 1.0f && left.stick.y == 1.0f);
        }
    } else if (!strcmp(name, "truncated")) {
        const unsigned lengths[] = {3, 5, 6, 20, 14};
        const uint8_t keys[] = {0x24, 0x63, 0x82, 0x41, 0x81};
        for (unsigned k = 0; k < 5; k++) {
            uint8_t raw[64];
            memset(raw, 0xff, sizeof(raw));
            raw[10] = 0;
            raw[24] = keys[k];
            for (unsigned bytes = 0; bytes < lengths[k]; bytes++) {
                handle_controller(&sys, raw, (uint8_t)(24 + bytes), now);
                REQUIRE(left.fusion.calls == 0);
                REQUIRE(left.buttons == 0 && left.trigger == 0 && left.grip == 0);
                REQUIRE(left.stick.x == 0 && left.stick.y == 0);
            }
        }
    } else {
        return 2;
    }
    return 0;
}

int main(int argc, char **argv) { return argc == 2 ? test_case(argv[1]) : 2; }
