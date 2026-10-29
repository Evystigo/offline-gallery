package com.evystigo.gallery

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.media.ExifInterface
import android.media.MediaMetadataRetriever
import java.io.IOException
import java.util.Locale
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive

private const val DEFAULT_SIMILARITY_DISTANCE = 6
private const val MAX_SIMILARITY_DISTANCE = 10

data class SimilarPhotoGroup(val items: List<MediaItem>, val isVideo: Boolean = false)

data class SimilarPhotoScanResult(
    val groups: List<SimilarPhotoGroup>,
    val photosChecked: Int,
    val videosChecked: Int,
    val unreadableFiles: List<String>,
)

object SimilarPhotoScanner {
    suspend fun findSimilarPhotos(
        context: Context,
        media: List<MediaItem>,
    ): SimilarPhotoScanResult {
        val candidates = media.filter { it.size > 0L }
        val unreadable = mutableListOf<String>()
        val photoHashes = mutableListOf<Pair<MediaItem, Long>>()
        val videoHashes = mutableListOf<Triple<MediaItem, Long, Double>>()
        var photosChecked = 0
        var videosChecked = 0

        for (item in candidates) {
            currentCoroutineContext().ensureActive()
            try {
                if (item.isVideo) {
                    videosChecked++
                    val signature = videoSignature(context, item)
                    if (signature != null) videoHashes.add(Triple(item, signature.first, signature.second))
                } else {
                    photosChecked++
                    val hash = perceptualHash(context, item)
                    if (hash != null) photoHashes.add(item to hash)
                }
            } catch (error: kotlinx.coroutines.CancellationException) {
                throw error
            } catch (error: IOException) {
                unreadable.add("${item.relativePath}: ${error.localizedMessage ?: "read failed"}")
            } catch (error: SecurityException) {
                unreadable.add("${item.relativePath}: ${error.localizedMessage ?: "access denied"}")
            }
        }

        val photoGroups = findSimilarHashGroups(photoHashes.map { it.second })
            .map { indexes -> SimilarPhotoGroup(indexes.map { photoHashes[it].first }) }
        val videoGroups = findSimilarHashGroups(
            hashes = videoHashes.map { it.second },
            mayMatch = { first, second ->
                durationsMatch(videoHashes[first].third, videoHashes[second].third)
            },
        ).map { indexes -> SimilarPhotoGroup(indexes.map { videoHashes[it].first }, isVideo = true) }
        val groups = (photoGroups + videoGroups)
            .sortedBy { group -> group.items.first().relativePath.lowercase(Locale.ROOT) }
        return SimilarPhotoScanResult(
            groups = groups,
            photosChecked = photosChecked,
            videosChecked = videosChecked,
            unreadableFiles = unreadable,
        )
    }

    internal fun differenceHash(luma: IntArray): Long? {
        if (luma.size != 72) return null
        var hash = 0L
        for (row in 0 until 8) {
            for (column in 0 until 8) {
                val index = row * 9 + column
                hash = (hash shl 1) or if (luma[index] > luma[index + 1]) 1L else 0L
            }
        }
        val setBits = java.lang.Long.bitCount(hash)
        return hash.takeIf { setBits in 6..58 }
    }

    internal fun findSimilarHashGroups(
        hashes: List<Long>,
        maxDistance: Int = DEFAULT_SIMILARITY_DISTANCE,
        mayMatch: (Int, Int) -> Boolean = { _, _ -> true },
    ): List<List<Int>> {
        if (hashes.size < 2) return emptyList()
        val distance = maxDistance.coerceIn(0, MAX_SIMILARITY_DISTANCE)
        val chunkCount = distance + 1
        val bounds = (0..chunkCount).map { index -> index * 64 / chunkCount }
        val buckets = List(chunkCount) { mutableMapOf<Long, MutableList<Int>>() }

        hashes.forEachIndexed { index, hash ->
            for (chunk in 0 until chunkCount) {
                val start = bounds[chunk]
                val width = bounds[chunk + 1] - start
                val mask = (1L shl width) - 1L
                val value = (hash ushr start) and mask
                buckets[chunk].getOrPut(value) { mutableListOf() }.add(index)
            }
        }

        val parent = IntArray(hashes.size) { it }
        fun root(index: Int): Int {
            var current = index
            while (parent[current] != current) {
                parent[current] = parent[parent[current]]
                current = parent[current]
            }
            return current
        }
        fun join(first: Int, second: Int) {
            val firstRoot = root(first)
            val secondRoot = root(second)
            if (firstRoot != secondRoot) parent[secondRoot] = firstRoot
        }

        val comparedPairs = mutableSetOf<Long>()
        buckets.forEach { chunkBuckets ->
            chunkBuckets.values.forEach { members ->
                for (firstPosition in 0 until members.size) {
                    for (secondPosition in firstPosition + 1 until members.size) {
                        val first = members[firstPosition]
                        val second = members[secondPosition]
                        val pairKey = (first.toLong() shl 32) or (second.toLong() and 0xffffffffL)
                        if (!comparedPairs.add(pairKey)) continue
                        if (java.lang.Long.bitCount(hashes[first] xor hashes[second]) <= distance &&
                            mayMatch(first, second)
                        ) {
                            join(first, second)
                        }
                    }
                }
            }
        }

        return hashes.indices.groupBy(::root).values.filter { it.size > 1 }
    }

    private fun videoSignature(context: Context, item: MediaItem): Pair<Long, Double>? {
        val retriever = MediaMetadataRetriever()
        try {
            retriever.setDataSource(context, item.uri)
            val durationSeconds = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)
                ?.toDoubleOrNull()
                ?.div(1000.0)
                ?.takeIf { it.isFinite() && it > 0.0 }
                ?: throw IOException("The video duration is unavailable.")
            val frameTimeUs = (durationSeconds * 500_000.0).toLong()
            val frame = retriever.getFrameAtTime(frameTimeUs, MediaMetadataRetriever.OPTION_CLOSEST_SYNC)
                ?: throw IOException("The provider could not decode a video frame.")
            return try {
                hashBitmap(frame)?.let { it to durationSeconds }
            } finally {
                frame.recycle()
            }
        } catch (error: IllegalArgumentException) {
            throw IOException("The video format could not be read.", error)
        } catch (error: IllegalStateException) {
            throw IOException("The video could not be read.", error)
        } finally {
            retriever.release()
        }
    }

    private fun durationsMatch(first: Double, second: Double): Boolean =
        kotlin.math.abs(first - second) <= maxOf(1.0, 0.02 * maxOf(first, second))

    private fun perceptualHash(context: Context, item: MediaItem): Long? {
        val resolver = context.contentResolver
        val orientation = resolver.openInputStream(item.uri)?.use { input ->
            ExifInterface(input).getAttributeInt(
                ExifInterface.TAG_ORIENTATION,
                ExifInterface.ORIENTATION_NORMAL,
            )
        } ?: throw IOException("The provider did not open the file.")

        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        resolver.openInputStream(item.uri)?.use { input ->
            BitmapFactory.decodeStream(input, null, bounds)
        } ?: throw IOException("The provider did not open the file.")
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) {
            throw IOException("The provider could not decode this image.")
        }

        var sampleSize = 1
        while (maxOf(bounds.outWidth, bounds.outHeight) / (sampleSize * 2) >= 512) {
            sampleSize *= 2
        }
        val options = BitmapFactory.Options().apply { inSampleSize = sampleSize }
        val decoded: Bitmap? = resolver.openInputStream(item.uri)?.use { input ->
            BitmapFactory.decodeStream(input, null, options)
        } ?: throw IOException("The provider did not open the file.")
        if (decoded == null) throw IOException("The provider could not decode this image.")

        val oriented = applyExifOrientation(decoded, orientation)
        val small = Bitmap.createScaledBitmap(oriented, 9, 8, true)
        if (small !== oriented && small !== decoded) oriented.recycle()
        if (decoded !== oriented) decoded.recycle()

        return try {
            hashBitmap(small)
        } finally {
            if (small !== oriented) small.recycle()
            oriented.recycle()
        }
    }

    private fun hashBitmap(bitmap: Bitmap): Long? {
        val small = Bitmap.createScaledBitmap(bitmap, 9, 8, true)
        return try {
            val pixels = IntArray(72)
            small.getPixels(pixels, 0, 9, 0, 0, 9, 8)
            val luma = IntArray(72) { index ->
                val color = pixels[index]
                (77 * ((color shr 16) and 0xff) +
                    150 * ((color shr 8) and 0xff) +
                    29 * (color and 0xff)) shr 8
            }
            differenceHash(luma)
        } finally {
            if (small !== bitmap) small.recycle()
        }
    }

    private fun applyExifOrientation(bitmap: Bitmap, orientation: Int): Bitmap {
        val matrix = Matrix()
        when (orientation) {
            ExifInterface.ORIENTATION_FLIP_HORIZONTAL -> matrix.setScale(-1f, 1f)
            ExifInterface.ORIENTATION_ROTATE_180 -> matrix.setRotate(180f)
            ExifInterface.ORIENTATION_FLIP_VERTICAL -> matrix.setScale(1f, -1f)
            ExifInterface.ORIENTATION_TRANSPOSE -> {
                matrix.setRotate(90f)
                matrix.postScale(-1f, 1f)
            }
            ExifInterface.ORIENTATION_ROTATE_90 -> matrix.setRotate(90f)
            ExifInterface.ORIENTATION_TRANSVERSE -> {
                matrix.setRotate(270f)
                matrix.postScale(-1f, 1f)
            }
            ExifInterface.ORIENTATION_ROTATE_270 -> matrix.setRotate(270f)
            else -> return bitmap
        }
        return Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height, matrix, true)
    }
}
