package com.evystigo.gallery

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class GallerySyncTest {
    @Test
    fun readsDesktopSyncFormatIncludingTombstonesAndMoves() {
        val input = JSONObject()
            .put("format", "gallery-sync")
            .put("version", 1)
            .put("machine_id", "desktop-123")
            .put(
                "media",
                org.json.JSONArray().put(
                    JSONObject()
                        .put("path", "Trips/one.jpg")
                        .put("size", 123L)
                        .put(
                            "tags",
                            org.json.JSONArray()
                                .put(
                                    JSONObject()
                                        .put("name", "summer trip")
                                        .put("updated_at", 1_800_000_000.5)
                                        .put("deleted", false),
                                )
                                .put(
                                    JSONObject()
                                        .put("name", "private")
                                        .put("updated_at", 1_800_000_001.0)
                                        .put("deleted", true),
                                ),
                        ),
                ),
            )
            .put(
                "moves",
                org.json.JSONArray().put(
                    JSONObject()
                        .put("from", "one.jpg")
                        .put("to", "Trips/one.jpg")
                        .put("size", 123L)
                        .put("at", 1_800_000_002.0),
                ),
            )

        val parsed = GallerySync.parseSyncFile(input.toString())

        assertEquals("desktop-123", parsed.machineId)
        assertEquals(SyncMediaRecord("Trips/one.jpg", 123L, mutableListOf(
            SyncTagRecord("summer trip", 1_800_000_000.5, false),
            SyncTagRecord("private", 1_800_000_001.0, true),
        )), parsed.media.single())
        assertEquals(SyncMoveRecord("one.jpg", "Trips/one.jpg", 123L, 1_800_000_002.0), parsed.moves.single())
    }

    @Test
    fun skipsUnsafePathsAndInvalidTagRecords() {
        val input = JSONObject()
            .put("format", "gallery-sync")
            .put("version", 1)
            .put("machine_id", "other-device")
            .put(
                "media",
                org.json.JSONArray()
                    .put(JSONObject().put("path", "../outside.jpg").put("size", 1L))
                    .put(
                        JSONObject()
                            .put("path", "photo.jpg")
                            .put("size", 2L)
                            .put(
                                "tags",
                                org.json.JSONArray()
                                    .put(JSONObject().put("name", "   ").put("updated_at", 1.0))
                                    .put(JSONObject().put("name", " valid ").put("updated_at", 2.0)),
                            ),
                    ),
            )

        val parsed = GallerySync.parseSyncFile(input.toString())

        assertEquals(1, parsed.media.size)
        assertEquals(SyncTagRecord("valid", 2.0, false), parsed.media.single().tags.single())
    }

    @Test
    fun rejectsUnknownFormatAndUnsafeRelativePaths() {
        val malformed = JSONObject().put("format", "other").put("version", 1).put("machine_id", "id")

        assertFalse(GallerySync.isSafeRelativePath("../outside.jpg"))
        assertFalse(GallerySync.isSafeRelativePath("a/../../outside.jpg"))
        assertFalse(GallerySync.isSafeRelativePath("C:/outside.jpg"))
        assertFalse(GallerySync.isSafeRelativePath("a\\outside.jpg"))
        assertTrue(GallerySync.isSafeRelativePath("Trips/one.jpg"))
        try {
            GallerySync.parseSyncFile(malformed.toString())
            throw AssertionError("Unsupported sync format should be rejected.")
        } catch (error: java.io.IOException) {
            assertTrue(error.message.orEmpty().contains("not a Gallery sync file"))
        }
    }
}
