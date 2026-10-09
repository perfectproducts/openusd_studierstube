// Rotator: a rotation angle that advances with an angular speed plus a constant angular
// acceleration. While `enabled` is false, angle and ramp are frozen.
//
//   omega          = speed + ramp
//   d(angle)/dt    = enabled ? omega        : 0
//   d(ramp)/dt     = enabled ? acceleration : 0
//
// The inputs are levels, not events: writing the same value again changes nothing, which is what
// a USD host does when it applies its authored attributes every step.
#include "config.h"
#include "model.h"

Status setStartValues(ModelInstance *comp) {
    ASSERT_NOT_NULL2(comp);

    M(angle)        = 0;
    M(ramp)         = 0;
    M(speed)        = 90;
    M(acceleration) = 0;
    M(enabled)      = true;

    comp->isDirtyValues = true;

    return OK;
}

Status calculateValues(ModelInstance *comp) {
    ASSERT_NOT_NULL2(comp);

    comp->isDirtyValues = false;

    return OK;
}

Status getFloat64(ModelInstance* comp, ValueReference vr, double values[], size_t nValues, size_t* index) {
    ASSERT_NOT_NULL2(comp);
    ASSERT_NOT_NULL2(values);
    ASSERT_NOT_NULL2(index);

    calculateValues(comp);

    switch (vr) {
        case vr_time:
            ASSERT_NVALUES(1);
            values[(*index)++] = comp->time;
            return OK;
        case vr_angle:
            ASSERT_NVALUES(1);
            values[(*index)++] = M(angle);
            return OK;
        case vr_der_angle:
            ASSERT_NVALUES(1);
            values[(*index)++] = M(enabled) ? M(speed) + M(ramp) : 0;
            return OK;
        case vr_ramp:
            ASSERT_NVALUES(1);
            values[(*index)++] = M(ramp);
            return OK;
        case vr_der_ramp:
            ASSERT_NVALUES(1);
            values[(*index)++] = M(enabled) ? M(acceleration) : 0;
            return OK;
        case vr_omega:
            ASSERT_NVALUES(1);
            values[(*index)++] = M(speed) + M(ramp);
            return OK;
        case vr_speed:
            ASSERT_NVALUES(1);
            values[(*index)++] = M(speed);
            return OK;
        case vr_acceleration:
            ASSERT_NVALUES(1);
            values[(*index)++] = M(acceleration);
            return OK;
        default:
            logError(comp, "Get Float64 is not allowed for value reference %u.", vr);
            return Error;
    }
}

Status setFloat64(ModelInstance* comp, ValueReference vr, const double values[], size_t nValues, size_t* index) {
    ASSERT_NOT_NULL2(comp);
    ASSERT_NOT_NULL2(values);
    ASSERT_NOT_NULL2(index);

    switch (vr) {

        case vr_angle:
            ASSERT_NVALUES(1);
            M(angle) = values[(*index)++];
            break;

        case vr_ramp:
            ASSERT_NVALUES(1);
            M(ramp) = values[(*index)++];
            break;

        case vr_speed:
            ASSERT_NVALUES(1);
            M(speed) = values[(*index)++];
            break;

        case vr_acceleration:
            ASSERT_NVALUES(1);
            M(acceleration) = values[(*index)++];
            break;

        default:
            logError(comp, "Unexpected value reference: %u.", vr);
            return Error;
    }

    comp->isDirtyValues = true;

    return OK;
}

Status getBoolean(ModelInstance* comp, ValueReference vr, bool values[], size_t nValues, size_t* index) {
    ASSERT_NOT_NULL2(comp);
    ASSERT_NOT_NULL2(values);
    ASSERT_NOT_NULL2(index);

    switch (vr) {
        case vr_enabled:
            ASSERT_NVALUES(1);
            values[(*index)++] = M(enabled);
            return OK;
        default:
            logError(comp, "Get Boolean is not allowed for value reference %u.", vr);
            return Error;
    }
}

Status setBoolean(ModelInstance* comp, ValueReference vr, const bool values[], size_t nValues, size_t* index) {
    ASSERT_NOT_NULL2(comp);
    ASSERT_NOT_NULL2(values);
    ASSERT_NOT_NULL2(index);

    switch (vr) {
        case vr_enabled:
            ASSERT_NVALUES(1);
            M(enabled) = values[(*index)++];
            break;
        default:
            logError(comp, "Set Boolean is not allowed for value reference %u.", vr);
            return Error;
    }

    comp->isDirtyValues = true;

    return OK;
}

size_t getNumberOfContinuousStates(ModelInstance* comp) {
    UNUSED(comp);
    return MAX_CONTINUOUS_STATES;
}

Status getContinuousStates(ModelInstance *comp, double x[], size_t nx) {
    ASSERT_NOT_NULL2(comp);
    ASSERT_NOT_NULL2(x);
    ASSERT_SIZE_T(nx, MAX_CONTINUOUS_STATES);

    calculateValues(comp);

    x[0] = M(angle);
    x[1] = M(ramp);

    return OK;
}

Status getNominalsOfContinuousStates(ModelInstance* comp, double nominals[], size_t nx) {
    ASSERT_NOT_NULL2(comp);
    ASSERT_NOT_NULL2(nominals);
    ASSERT_SIZE_T(nx, MAX_CONTINUOUS_STATES);

    nominals[0] = 360.0;
    nominals[1] = 90.0;

    return OK;
}

Status setContinuousStates(ModelInstance *comp, const double x[], size_t nx) {
    ASSERT_NOT_NULL2(comp);
    ASSERT_NOT_NULL2(x);
    ASSERT_SIZE_T(nx, MAX_CONTINUOUS_STATES);

    M(angle) = x[0];
    M(ramp)  = x[1];

    comp->isDirtyValues = true;

    return OK;
}

Status getDerivatives(ModelInstance *comp, double dx[], size_t nx) {
    ASSERT_NOT_NULL2(comp);
    ASSERT_NOT_NULL2(dx);
    ASSERT_SIZE_T(nx, MAX_CONTINUOUS_STATES);

    calculateValues(comp);

    dx[0] = M(enabled) ? M(speed) + M(ramp) : 0;
    dx[1] = M(enabled) ? M(acceleration) : 0;

    return OK;
}
