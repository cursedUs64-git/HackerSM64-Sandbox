#ifndef PAINTING_ARRAY_TABLE_H
#define PAINTING_ARRAY_TABLE_H

#include "types.h"

#define PAINTING_GROUP_MAX 10 // paintings per group

struct __attribute__((aligned(16))) PaintingGroup {
    u16 level;
    u16 area;
    struct Painting *paintings[PAINTING_GROUP_MAX + 1]; // + 1 for NULL
};

#define PAINTING_GROUP_RECORD_SIZE sizeof(struct PaintingGroup)

STATIC_ASSERT((PAINTING_GROUP_MAX % 2) == 0, "PAINTING_GROUP_MAX must be a multiple of 2.");
STATIC_ASSERT((sizeof(struct PaintingGroup) % 16) == 0, "PaintingGroup must be aligned by 16.");

#endif
