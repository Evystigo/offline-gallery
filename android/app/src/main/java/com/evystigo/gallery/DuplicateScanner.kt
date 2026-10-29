package com.evystigo.gallery

import android.content.Context
import java.io.IOException
import java.security.MessageDigest
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive

data class DuplicateGroup(
    val size: Long,
    val items: List<MediaItem>,
)

data class DuplicateScanResult(
    val groups: List<DuplicateGroup>,
    val filesHashed: Int,
    val unknownSizeFiles: Int,
    val unreadableFiles: List<String>,
)

object DuplicateScanner {
    private data class ContentKey(val size: Long, val sha256: String)

    suspend fun findExactDuplicates(
        context: Context,
        media: List<MediaItem>,
    ): DuplicateScanResult {
        val candidates = media.filter { it.size > 0L }
            .groupBy { it.size }
            .values
            .filter { it.size > 1 }
            .flatten()
        val unreadable = mutableListOf<String>()
        val byContent = mutableMapOf<ContentKey, MutableList<MediaItem>>()
        var hashed = 0
        val buffer = ByteArray(DEFAULT_BUFFER_SIZE)

        for (item in candidates) {
            currentCoroutineContext().ensureActive()
            try {
                val digest = MessageDigest.getInstance("SHA-256")
                val stream = context.contentResolver.openInputStream(item.uri)
                    ?: throw IOException("The provider did not open the file.")
                stream.use { input ->
                    while (true) {
                        currentCoroutineContext().ensureActive()
                        val count = input.read(buffer)
                        if (count < 0) break
                        if (count > 0) digest.update(buffer, 0, count)
                    }
                }
                hashed++
                val key = ContentKey(item.size, digest.digest().joinToString("") { "%02x".format(it) })
                byContent.getOrPut(key) { mutableListOf() }.add(item)
            } catch (error: kotlinx.coroutines.CancellationException) {
                throw error
            } catch (error: IOException) {
                unreadable.add("${item.relativePath}: ${error.localizedMessage ?: "read failed"}")
            } catch (error: SecurityException) {
                unreadable.add("${item.relativePath}: ${error.localizedMessage ?: "access denied"}")
            }
        }

        val groups = byContent.values
            .filter { it.size > 1 }
            .map { items -> DuplicateGroup(items.first().size, items) }
            .sortedWith(compareByDescending<DuplicateGroup> { it.size }.thenBy { it.items.first().relativePath })

        return DuplicateScanResult(
            groups = groups,
            filesHashed = hashed,
            unknownSizeFiles = media.count { it.size <= 0L },
            unreadableFiles = unreadable,
        )
    }
}
