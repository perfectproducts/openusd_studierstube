#ifndef config_h
#define config_h

#include <stdbool.h>

// define class name and unique id
#define MODEL_IDENTIFIER Rotator
#define INSTANTIATION_TOKEN "{DDB23E2B-68A2-4972-B5E7-953827D35CD7}"

#define CO_SIMULATION

#define MAX_CONTINUOUS_STATES 2

#define SET_FLOAT64
#define SET_BOOLEAN
#define GET_BOOLEAN

#define FIXED_SOLVER_STEP 1e-3
#define DEFAULT_STOP_TIME 10

typedef enum {
    vr_time, vr_angle, vr_der_angle, vr_ramp, vr_der_ramp, vr_omega, vr_speed, vr_acceleration, vr_enabled
} ValueReference;

typedef struct {

    double angle;         // deg, state
    double ramp;          // deg/s, state: speed gained from the acceleration
    double speed;         // deg/s, input
    double acceleration;  // deg/s2, input
    bool enabled;         // input: false freezes angle and ramp

} ModelData;

#endif /* config_h */
