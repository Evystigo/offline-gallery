package com.evystigo.gallery

import java.time.LocalDate
import java.time.ZoneId
import java.time.DateTimeException
import java.util.Locale

data class SearchDocument(
    val relativePath: String,
    val isVideo: Boolean,
    val lastModified: Long,
    val tags: List<String>,
)

data class GallerySearchQuery(
    private val predicates: List<(SearchDocument) -> Boolean>,
    val error: String? = null,
) {
    fun matches(document: SearchDocument): Boolean =
        error == null && predicates.all { it(document) }
}

fun MediaItem.toSearchDocument() = SearchDocument(
    relativePath = relativePath,
    isVideo = isVideo,
    lastModified = lastModified,
    tags = tags,
)

fun parseGallerySearch(text: String): GallerySearchQuery {
    val tokens = tokenize(text)
    val predicates = mutableListOf<(SearchDocument) -> Boolean>()

    for (token in tokens) {
        val lower = token.lowercase(Locale.ROOT)
        when {
            token.startsWith("-#") && token.length > 2 -> {
                val excluded = normalizeTag(token.drop(2))
                predicates.add { document -> document.tags.none { it.equals(excluded, true) } }
            }

            token.startsWith("#") && token.length > 1 -> {
                val wanted = token.drop(1).split('|')
                    .map(::normalizeTag)
                    .filter(String::isNotEmpty)
                if (wanted.isNotEmpty()) {
                    predicates.add { document ->
                        wanted.any { tag -> document.tags.any { it.equals(tag, true) } }
                    }
                }
            }

            lower.startsWith("type:") -> {
                val type = lower.removePrefix("type:")
                if (type != "photo" && type != "video") {
                    return GallerySearchQuery(emptyList(), "type: must be photo or video.")
                }
                predicates.add { document -> document.isVideo == (type == "video") }
            }

            lower.startsWith("album:") && token.length > 6 -> {
                val album = token.substring(6).trimEnd('/')
                if (album.isNotEmpty()) {
                    predicates.add { document ->
                        val parent = parentAlbum(document.relativePath)
                        parent.equals(album, ignoreCase = true) ||
                            parent.startsWith("$album/", ignoreCase = true)
                    }
                }
            }

            lower.startsWith("after:") -> {
                val start = parseDate(token.substring(6))
                    ?: return GallerySearchQuery(emptyList(), "Bad date in after:, expected YYYY-MM-DD.")
                predicates.add { document -> document.lastModified >= start }
            }

            lower.startsWith("before:") -> {
                val end = parseDate(token.substring(7), endExclusive = true)
                    ?: return GallerySearchQuery(emptyList(), "Bad date in before:, expected YYYY-MM-DD.")
                predicates.add { document -> document.lastModified < end }
            }

            else -> predicates.add { document ->
                document.relativePath.contains(token, ignoreCase = true)
            }
        }
    }
    return GallerySearchQuery(predicates)
}

private fun tokenize(text: String): List<String> {
    val result = mutableListOf<String>()
    val token = StringBuilder()
    var quoted = false
    var escaping = false
    var started = false

    for (character in text) {
        if (escaping) {
            token.append(character)
            escaping = false
            started = true
        } else if (quoted && character == '\\') {
            escaping = true
        } else if (character == '"') {
            quoted = !quoted
            started = true
        } else if (character.isWhitespace() && !quoted) {
            if (started) {
                result.add(token.toString())
                token.clear()
                started = false
            }
        } else {
            token.append(character)
            started = true
        }
    }

    if (quoted) return text.replace("\"", "").split(Regex("\\s+")).filter(String::isNotEmpty)
    if (escaping) token.append('\\')
    if (started) result.add(token.toString())
    return result
}

private fun parseDate(value: String, endExclusive: Boolean = false): Long? = try {
    val date = LocalDate.parse(value)
    (if (endExclusive) date.plusDays(1) else date)
        .atStartOfDay(ZoneId.systemDefault())
        .toInstant()
        .toEpochMilli()
} catch (_: DateTimeException) {
    null
}

private fun normalizeTag(tag: String) = tag.trim().split(Regex("\\s+"))
    .filter(String::isNotEmpty)
    .joinToString(" ")

private fun parentAlbum(path: String): String =
    path.substringBeforeLast('/', missingDelimiterValue = "")
