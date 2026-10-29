package com.evystigo.gallery

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SimilarPhotoScannerTest {
    @Test
    fun differenceHashUsesAdjacentLumaAndRejectsFlatImages() {
        val pixels = IntArray(72)
        for (row in 0 until 8) {
            for (column in 0 until 9) {
                pixels[row * 9 + column] = if (row < 4) 8 - column else column
            }
        }

        assertEquals(0xffff_ffff_0000_0000UL.toLong(), SimilarPhotoScanner.differenceHash(pixels))
        assertNull(SimilarPhotoScanner.differenceHash(IntArray(72)))
        assertNull(SimilarPhotoScanner.differenceHash(IntArray(71)))
    }

    @Test
    fun similarHashesAreGroupedByDistanceAndConnectedChains() {
        val base = 0x0f0f_3c3c_a5a5_9669L
        val fourAway = base xor 0x0fL
        val eightFromBase = base xor 0xffL
        val distinct = base xor -1L

        val groups = SimilarPhotoScanner.findSimilarHashGroups(
            listOf(base, fourAway, eightFromBase, distinct),
            maxDistance = 4,
        )

        assertEquals(1, groups.size)
        assertEquals(setOf(0, 1, 2), groups.single().toSet())
    }

    @Test
    fun similarityDistanceIsClampedAndSingleHashesDoNotMakeGroups() {
        val base = 0x0f0f_3c3c_a5a5_9669L
        val oneBitAway = base xor 1L

        assertTrue(SimilarPhotoScanner.findSimilarHashGroups(listOf(base)).isEmpty())
        assertTrue(
            SimilarPhotoScanner.findSimilarHashGroups(
                listOf(base, oneBitAway),
                maxDistance = -1,
            ).isEmpty(),
        )
        assertEquals(
            1,
            SimilarPhotoScanner.findSimilarHashGroups(
                listOf(base, base xor 0x3ffL),
                maxDistance = 100,
            ).size,
        )
    }

    @Test
    fun videoLikeHashesOnlyGroupWhenTheirDurationsAreClose() {
        val hash = 0x0f0f_3c3c_a5a5_9669L
        val durations = listOf(50.0, 51.0, 60.0)

        val groups = SimilarPhotoScanner.findSimilarHashGroups(
            hashes = listOf(hash, hash, hash),
            mayMatch = { first, second ->
                val a = durations[first]
                val b = durations[second]
                kotlin.math.abs(a - b) <= maxOf(1.0, 0.02 * maxOf(a, b))
            },
        )

        assertEquals(1, groups.size)
        assertEquals(setOf(0, 1), groups.single().toSet())
    }
}
