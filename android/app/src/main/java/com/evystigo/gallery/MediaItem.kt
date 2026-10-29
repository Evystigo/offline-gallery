package com.evystigo.gallery

import android.net.Uri

data class MediaItem(
    val uri: Uri,
    val parentUri: Uri,
    val name: String,
    val relativePath: String,
    val isVideo: Boolean,
    val size: Long,
    val lastModified: Long,
    val tags: List<String> = emptyList(),
)

data class LibraryScan(
    val media: List<MediaItem>,
    val folders: List<String>,
    val folderUris: Map<String, Uri>,
)
