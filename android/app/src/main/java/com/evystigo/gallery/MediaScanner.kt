package com.evystigo.gallery

import android.content.Context
import android.net.Uri
import android.provider.DocumentsContract
import java.io.IOException
import java.util.ArrayDeque
import java.util.Locale

object MediaScanner {
    private val photoExtensions = setOf(
        "jpg", "jpeg", "png", "gif", "bmp", "webp", "tif", "tiff", "heic", "heif",
    )
    private val videoExtensions = setOf(
        "mp4", "mkv", "mov", "webm", "avi", "m4v", "wmv", "flv", "mpg", "mpeg", "3gp",
    )

    private data class Folder(val documentId: String, val relativePath: String, val uri: Uri)

    fun scan(context: Context, treeUri: Uri): LibraryScan {
        val resolver = context.contentResolver
        val rootId = DocumentsContract.getTreeDocumentId(treeUri)
        val pending = ArrayDeque<Folder>()
        val visited = mutableSetOf<String>()
        val results = mutableListOf<MediaItem>()
        val folders = mutableSetOf<String>()
        val folderUris = mutableMapOf<String, Uri>()
        val rootUri = DocumentsContract.buildDocumentUriUsingTree(treeUri, rootId)
        folderUris[""] = rootUri
        pending.addLast(Folder(rootId, "", rootUri))

        while (pending.isNotEmpty()) {
            val folder = pending.removeLast()
            if (!visited.add(folder.documentId)) continue

            val childrenUri = DocumentsContract.buildChildDocumentsUriUsingTree(
                treeUri,
                folder.documentId,
            )
            val projection = arrayOf(
                DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                DocumentsContract.Document.COLUMN_DISPLAY_NAME,
                DocumentsContract.Document.COLUMN_MIME_TYPE,
                DocumentsContract.Document.COLUMN_SIZE,
                DocumentsContract.Document.COLUMN_LAST_MODIFIED,
            )
            val cursor = resolver.query(childrenUri, projection, null, null, null)
                ?: throw IOException("The selected folder provider did not return its contents.")

            cursor.use {
                val idColumn = it.getColumnIndex(DocumentsContract.Document.COLUMN_DOCUMENT_ID)
                val nameColumn = it.getColumnIndex(DocumentsContract.Document.COLUMN_DISPLAY_NAME)
                val mimeColumn = it.getColumnIndex(DocumentsContract.Document.COLUMN_MIME_TYPE)
                val sizeColumn = it.getColumnIndex(DocumentsContract.Document.COLUMN_SIZE)
                val modifiedColumn =
                    it.getColumnIndex(DocumentsContract.Document.COLUMN_LAST_MODIFIED)
                if (idColumn < 0 || nameColumn < 0 || mimeColumn < 0) {
                    throw IOException("The selected folder provider returned incomplete file information.")
                }

                while (it.moveToNext()) {
                    val name = it.getString(nameColumn) ?: continue
                    if (name.startsWith(".")) continue

                    val documentId = it.getString(idColumn) ?: continue
                    val mimeType = it.getString(mimeColumn)
                    val relativePath = if (folder.relativePath.isEmpty()) {
                        name
                    } else {
                        "${folder.relativePath}/$name"
                    }

                    if (mimeType == DocumentsContract.Document.MIME_TYPE_DIR) {
                        val folderUri =
                            DocumentsContract.buildDocumentUriUsingTree(treeUri, documentId)
                        folders.add(relativePath)
                        folderUris[relativePath] = folderUri
                        pending.addLast(Folder(documentId, relativePath, folderUri))
                        continue
                    }

                    val isVideo = isVideo(name, mimeType) ?: continue
                    results.add(
                        MediaItem(
                            uri = DocumentsContract.buildDocumentUriUsingTree(treeUri, documentId),
                            parentUri = folder.uri,
                            name = name,
                            relativePath = relativePath,
                            isVideo = isVideo,
                            size = if (sizeColumn >= 0 && !it.isNull(sizeColumn)) {
                                it.getLong(sizeColumn)
                            } else {
                                0L
                            },
                            lastModified = if (modifiedColumn >= 0 && !it.isNull(modifiedColumn)) {
                                it.getLong(modifiedColumn)
                            } else {
                                0L
                            },
                        ),
                    )
                }
            }
        }

        return LibraryScan(
            media = results.sortedWith(
                compareByDescending<MediaItem> { it.lastModified }
                    .thenBy { it.relativePath.lowercase(Locale.ROOT) },
            ),
            folders = folders.sortedBy { it.lowercase(Locale.ROOT) },
            folderUris = folderUris,
        )
    }

    private fun isVideo(name: String, mimeType: String?): Boolean? {
        val extension = name.substringAfterLast('.', "").lowercase(Locale.ROOT)
        return when {
            extension in videoExtensions || mimeType?.startsWith("video/") == true -> true
            extension in photoExtensions || mimeType?.startsWith("image/") == true -> false
            else -> null
        }
    }
}
