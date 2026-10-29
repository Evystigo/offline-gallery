package com.evystigo.gallery

fun albumContainsMedia(relativePath: String, albumPath: String): Boolean =
    albumPath.isEmpty() || relativePath.equals(albumPath, ignoreCase = true) ||
        relativePath.startsWith("$albumPath/", ignoreCase = true)

fun albumContainsFolder(folderPath: String, albumPath: String): Boolean =
    albumPath.isEmpty() || folderPath.startsWith("$albumPath/", ignoreCase = true)

fun isPhotoInAlbum(relativePath: String, isVideo: Boolean, albumPath: String): Boolean {
    if (isVideo) return false
    val parentPath = relativePath.substringBeforeLast('/', missingDelimiterValue = "")
    return albumContainsMedia(parentPath, albumPath)
}
