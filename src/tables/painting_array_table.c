#include <ultra64.h>
#include "types.h"
#include "painting_array_table.h"
#include "include/level_table.h"

// Add your include headers containing externs to your paintings here!
// e.g.: #include "levels/.../header.h"

/**
 * Sorted by level value, then area value.
 * (for maximum efficiency of the cache line, and is also necessary)
 * End each group with a NULL. (stop_other_paintings needs it)
 * Syntax:
 * { LEVEL_NAME, AREANUMBER,
 *     { &a_painting, &b_painting, ..., NULL } },
 * ...
 */
const struct PaintingGroup gPaintingGroups[]
    __attribute__((section(".painting_table"), aligned(16))) = {

};
