package com.evystigo.gallery

import java.time.LocalDate
import java.time.ZoneId
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class GallerySearchTest {
    private val beachPhoto = SearchDocument(
        relativePath = "Trips/2024/Sunset Beach.jpg",
        isVideo = false,
        lastModified = millis("2024-06-15"),
        tags = listOf("Beach", "New York"),
    )

    @Test
    fun quotedTagsCanContainSpaces() {
        assertTrue(parseGallerySearch("#\"new york\"").matches(beachPhoto))
        assertFalse(parseGallerySearch("#\"new jersey\"").matches(beachPhoto))
    }

    @Test
    fun alternativesAndExcludedTagsMatchCaseInsensitively() {
        assertTrue(parseGallerySearch("#mountain|beach -#nsfw").matches(beachPhoto))
        assertFalse(parseGallerySearch("#mountain|desert").matches(beachPhoto))
        assertFalse(parseGallerySearch("-#BEACH").matches(beachPhoto))
    }

    @Test
    fun albumFilterIncludesNestedFoldersButNotNamePrefixes() {
        assertTrue(parseGallerySearch("album:Trips").matches(beachPhoto))
        assertTrue(parseGallerySearch("album:Trips/2024").matches(beachPhoto))
        assertFalse(parseGallerySearch("album:Trip").matches(beachPhoto))
    }

    @Test
    fun quotedTextMatchesAsOnePhrase() {
        assertTrue(parseGallerySearch("\"Sunset Beach\"").matches(beachPhoto))
        assertFalse(parseGallerySearch("\"Beach Sunset\"").matches(beachPhoto))
    }

    @Test
    fun typeAndLocalDateRangesCanBeCombined() {
        val query = parseGallerySearch("type:photo after:2024-06-15 before:2024-06-15")
        assertTrue(query.matches(beachPhoto))
        assertFalse(parseGallerySearch("type:video").matches(beachPhoto))
        assertFalse(parseGallerySearch("after:2024-06-16").matches(beachPhoto))
        assertTrue(parseGallerySearch("before:2024-06-15").matches(beachPhoto))
        assertFalse(parseGallerySearch("before:2024-06-14").matches(beachPhoto))
    }

    @Test
    fun invalidFiltersAreReportedAndDoNotMatch() {
        val invalidType = parseGallerySearch("type:audio")
        assertEquals("type: must be photo or video.", invalidType.error)
        assertFalse(invalidType.matches(beachPhoto))

        val invalidDate = parseGallerySearch("after:2024-13-40")
        assertEquals("Bad date in after:, expected YYYY-MM-DD.", invalidDate.error)
        assertFalse(invalidDate.matches(beachPhoto))
    }

    private fun millis(date: String): Long = LocalDate.parse(date)
        .atStartOfDay(ZoneId.systemDefault())
        .toInstant()
        .toEpochMilli()
}
