package com.evystigo.gallery

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AlbumPathsTest {
    @Test
    fun rootAlbumContainsMediaFromEverySubdirectory() {
        assertTrue(albumContainsMedia("photo.jpg", ""))
        assertTrue(albumContainsMedia("Trips/2024/photo.jpg", ""))
    }

    @Test
    fun nestedAlbumContainsItsOwnAndDescendantMediaOnly() {
        assertTrue(albumContainsMedia("Trips/photo.jpg", "Trips"))
        assertTrue(albumContainsMedia("Trips/2024/photo.jpg", "Trips"))
        assertTrue(albumContainsMedia("trips/2024/photo.jpg", "Trips"))
        assertFalse(albumContainsMedia("TripsOld/photo.jpg", "Trips"))
        assertFalse(albumContainsMedia("Other/photo.jpg", "Trips"))
    }

    @Test
    fun folderDescendantCheckUsesPathBoundaries() {
        assertTrue(albumContainsFolder("Trips/2024", "Trips"))
        assertFalse(albumContainsFolder("TripsOld/2024", "Trips"))
    }

    @Test
    fun folderPhotoSelectionIncludesNestedPhotosButNotVideosOrSiblingFolders() {
        assertTrue(isPhotoInAlbum("Trips/photo.jpg", false, "Trips"))
        assertTrue(isPhotoInAlbum("Trips/2024/photo.jpg", false, "Trips"))
        assertFalse(isPhotoInAlbum("Trips/video.mp4", true, "Trips"))
        assertFalse(isPhotoInAlbum("TripsOld/photo.jpg", false, "Trips"))
        assertFalse(isPhotoInAlbum("Other/photo.jpg", false, "Trips"))
        assertTrue(isPhotoInAlbum("root.jpg", false, ""))
    }
}
