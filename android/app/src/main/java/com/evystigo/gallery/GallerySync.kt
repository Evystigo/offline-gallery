package com.evystigo.gallery

import android.content.Context
import android.net.Uri
import android.provider.DocumentsContract
import androidx.documentfile.provider.DocumentFile
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.nio.charset.StandardCharsets
import java.util.Locale
import java.util.UUID
import org.json.JSONArray
import org.json.JSONObject

private const val SYNC_FORMAT = "gallery-sync"
private const val SYNC_VERSION = 1
private const val SYNC_FILE_PREFIX = "gallery-sync."
private const val MAX_SYNC_BYTES = 50 * 1024 * 1024
private const val MOVE_RETENTION_SECONDS = 90 * 24 * 60 * 60.0

data class GallerySyncResult(
    val filesRead: Int,
    val tagsChanged: Int,
    val recordsHeld: Int,
    val movesApplied: Int,
    val movesSkipped: List<String>,
    val errors: List<String>,
)

internal data class IncomingSyncFile(
    val machineId: String,
    val media: List<SyncMediaRecord>,
    val moves: List<SyncMoveRecord>,
)

object GallerySync {
    fun sync(
        context: Context,
        database: GalleryDatabase,
        libraryUri: Uri,
        syncFolderUri: Uri,
        applyMoves: Boolean,
    ): GallerySyncResult {
        val libraryKey = libraryUri.toString()
        val syncFolder = DocumentFile.fromTreeUri(context, syncFolderUri)
            ?: throw IOException("The selected sync folder is unavailable.")
        if (!syncFolder.isDirectory || !syncFolder.canRead() || !syncFolder.canWrite()) {
            throw IOException("The selected sync folder must allow reading and writing.")
        }

        val ownMachineId = database.machineId()
        val errors = mutableListOf<String>()
        val files = readSyncFiles(context, syncFolder, ownMachineId, errors)
        val movesSkipped = mutableListOf<String>()
        var movesApplied = 0
        val remappedPaths = mutableMapOf<String, String>()
        val localMedia = MediaScanner.scan(context, libraryUri).media.toMutableList()
        database.updateLibrary(libraryKey, localMedia)

        if (applyMoves) {
            files.flatMap { it.moves }
                .sortedBy { it.at }
                .forEach { move ->
                    val outcome = applyMove(
                        context,
                        database,
                        libraryUri,
                        localMedia,
                        move,
                    )
                    if (outcome.error != null) {
                        movesSkipped.add(outcome.error)
                    } else if (outcome.applied) {
                        movesApplied++
                        remappedPaths[move.from] = outcome.destinationPath ?: move.to
                    } else if (outcome.destinationPath != null) {
                        remappedPaths[move.from] = outcome.destinationPath
                    }
                }
        }
        database.updateLibrary(libraryKey, localMedia)

        var tagsChanged = 0
        var recordsHeld = 0
        val mediaByPath = localMedia.associateBy { it.relativePath.lowercase(Locale.ROOT) }
        val byNameAndSize = localMedia.groupBy {
            it.name.lowercase(Locale.ROOT) to it.size
        }
        files.flatMap { it.media }.forEach { record ->
            val remappedPath = remappedPaths[record.path] ?: record.path
            val candidate = mediaByPath[remappedPath.lowercase(Locale.ROOT)]
                ?.takeIf { it.size == record.size }
                ?: byNameAndSize[record.path.substringAfterLast('/').lowercase(Locale.ROOT) to record.size]
                    ?.singleOrNull()
            record.tags.forEach { tag ->
                val path = candidate?.relativePath ?: remappedPath
                if (database.mergeSyncTag(libraryKey, path, tag.name, tag.updatedAt, tag.deleted)) {
                    if (candidate == null) recordsHeld++ else tagsChanged++
                }
            }
        }
        recordsHeld += database.attachPending(libraryKey)

        writeOwnExport(context, syncFolder, ownMachineId, database, libraryKey)
        return GallerySyncResult(
            filesRead = files.size,
            tagsChanged = tagsChanged,
            recordsHeld = recordsHeld,
            movesApplied = movesApplied,
            movesSkipped = movesSkipped,
            errors = errors,
        )
    }

    private data class MoveOutcome(
        val applied: Boolean = false,
        val destinationPath: String? = null,
        val error: String? = null,
    )

    private fun applyMove(
        context: Context,
        database: GalleryDatabase,
        libraryUri: Uri,
        media: MutableList<MediaItem>,
        move: SyncMoveRecord,
    ): MoveOutcome {
        val label = "${move.from} -> ${move.to}"
        if (!isSafeRelativePath(move.from) || !isSafeRelativePath(move.to) ||
            move.size < 0L || !move.at.isFinite()
        ) {
            return MoveOutcome(error = "$label: malformed move record")
        }
        if (database.knownMove(libraryUri.toString(), move)) return MoveOutcome()

        val sourceParent = parentRelativePath(move.from)
        val destinationParent = parentRelativePath(move.to)
        val sourceName = move.from.substringAfterLast('/')
        val destinationName = move.to.substringAfterLast('/')
        val sameDirectory = sourceParent.equals(destinationParent, ignoreCase = true)
        val sameName = sourceName.equals(destinationName, ignoreCase = true)
        if (sameDirectory && sameName) return MoveOutcome()
        if (!sameDirectory && !sameName) {
            return MoveOutcome(error = "$label: moving and renaming together is not supported")
        }

        val source = media.firstOrNull { it.relativePath.equals(move.from, ignoreCase = true) }
        if (source == null) {
            val destination = media.firstOrNull {
                it.relativePath.equals(move.to, ignoreCase = true) && it.size == move.size
            }
            if (destination != null) {
                database.recordMove(libraryUri.toString(), move.from, move.to, move.size, move.at)
                return MoveOutcome(destinationPath = destination.relativePath)
            }
            return MoveOutcome()
        }
        if (source.size != move.size) {
            return MoveOutcome(error = "$label: file size no longer matches")
        }

        return try {
            val resolver = context.contentResolver
            val renamedUri: Uri
            val destinationParentUri: Uri
            val newName: String
            if (sameDirectory) {
                renamedUri = DocumentsContract.renameDocument(resolver, source.uri, destinationName)
                    ?: throw IOException("The provider did not complete the rename.")
                destinationParentUri = source.parentUri
                newName = DocumentFile.fromSingleUri(context, renamedUri)?.name
                    ?: destinationName
            } else {
                destinationParentUri = ensureFolderPath(context, libraryUri, destinationParent)
                if (media.any { it.relativePath.equals(move.to, ignoreCase = true) }) {
                    return MoveOutcome(error = "$label: destination already contains a file")
                }
                renamedUri = DocumentsContract.moveDocument(
                    resolver,
                    source.uri,
                    source.parentUri,
                    destinationParentUri,
                ) ?: throw IOException("The provider did not complete the move.")
                newName = source.name
            }

            val actualPath = if (destinationParent.isEmpty()) newName else "$destinationParent/$newName"
            database.moveTags(
                libraryUri.toString(),
                source.relativePath,
                actualPath,
                logMove = false,
            )
            database.recordMove(libraryUri.toString(), move.from, move.to, move.size, move.at)
            media.remove(source)
            media.add(
                source.copy(
                    uri = renamedUri,
                    parentUri = destinationParentUri,
                    name = newName,
                    relativePath = actualPath,
                ),
            )
            MoveOutcome(applied = true, destinationPath = actualPath)
        } catch (error: SecurityException) {
            MoveOutcome(error = "$label: ${error.localizedMessage ?: "access denied"}")
        } catch (error: IOException) {
            MoveOutcome(error = "$label: ${error.localizedMessage ?: "file operation failed"}")
        } catch (error: UnsupportedOperationException) {
            MoveOutcome(error = "$label: storage provider does not support this operation")
        }
    }

    private fun ensureFolderPath(context: Context, rootUri: Uri, relativePath: String): Uri {
        var parent = DocumentFile.fromTreeUri(context, rootUri)
            ?: throw IOException("The selected library folder is unavailable.")
        if (relativePath.isEmpty()) return parent.uri
        relativePath.split('/').forEach { segment ->
            val existing = parent.findFile(segment)
            val folder = existing ?: parent.createDirectory(segment)
                ?: throw IOException("The provider could not create folder '$segment'.")
            if (!folder.isDirectory) throw IOException("'$segment' is not a folder.")
            parent = folder
        }
        return parent.uri
    }

    private fun readSyncFiles(
        context: Context,
        folder: DocumentFile,
        ownMachineId: String,
        errors: MutableList<String>,
    ): List<IncomingSyncFile> {
        val files = mutableListOf<IncomingSyncFile>()
        folder.listFiles()
            .filter {
                val name = it.name
                it.isFile && name != null && name.startsWith(SYNC_FILE_PREFIX) && name.endsWith(".json")
            }
            .sortedBy { it.name.orEmpty() }
            .forEach { file ->
                try {
                    val bytes = context.contentResolver.openInputStream(file.uri)?.use { input ->
                        val output = ByteArrayOutputStream()
                        val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
                        var total = 0
                        while (true) {
                            val count = input.read(buffer)
                            if (count < 0) break
                            total += count
                            if (total > MAX_SYNC_BYTES) throw IOException("sync file exceeds 50 MiB")
                            output.write(buffer, 0, count)
                        }
                        output.toByteArray()
                    } ?: throw IOException("provider did not open the file")
                    val parsed = parseSyncFile(String(bytes, StandardCharsets.UTF_8))
                    if (parsed.machineId != ownMachineId) files.add(parsed)
                } catch (error: Exception) {
                    if (error is kotlinx.coroutines.CancellationException) throw error
                    errors.add("${file.name}: ${error.localizedMessage ?: "could not read sync file"}")
                }
            }
        return files
    }

    internal fun parseSyncFile(text: String): IncomingSyncFile {
        val root = JSONObject(text)
        if (root.optString("format") != SYNC_FORMAT) throw IOException("not a Gallery sync file")
        val version = root.optInt("version", -1)
        if (version < 1 || version > SYNC_VERSION) {
            throw IOException("unsupported sync file version $version")
        }
        val machineId = root.optString("machine_id")
        if (machineId.isBlank() || machineId.length > 128) throw IOException("missing machine id")

        val media = mutableListOf<SyncMediaRecord>()
        val mediaArray = root.optJSONArray("media") ?: JSONArray()
        for (index in 0 until mediaArray.length()) {
            val item = mediaArray.optJSONObject(index) ?: continue
            val path = item.optString("path")
            val size = item.optLong("size", -1L)
            if (!isSafeRelativePath(path) || size < 0L) continue
            val tags = mutableListOf<SyncTagRecord>()
            val tagArray = item.optJSONArray("tags") ?: JSONArray()
            for (tagIndex in 0 until tagArray.length()) {
                val tag = tagArray.optJSONObject(tagIndex) ?: continue
                val name = tag.optString("name").trim().replace(Regex("\\s+"), " ")
                val updatedAt = tag.optDouble("updated_at", Double.NaN)
                if (name.isBlank() || name.length > 1024 || !updatedAt.isFinite()) continue
                tags.add(SyncTagRecord(name, updatedAt, tag.optBoolean("deleted", false)))
            }
            media.add(SyncMediaRecord(path, size, tags))
        }

        val moves = mutableListOf<SyncMoveRecord>()
        val moveArray = root.optJSONArray("moves") ?: JSONArray()
        for (index in 0 until moveArray.length()) {
            val item = moveArray.optJSONObject(index) ?: continue
            moves.add(
                SyncMoveRecord(
                    from = item.optString("from"),
                    to = item.optString("to"),
                    size = item.optLong("size", -1L),
                    at = item.optDouble("at", Double.NaN),
                ),
            )
        }
        return IncomingSyncFile(machineId, media, moves)
    }

    private fun writeOwnExport(
        context: Context,
        folder: DocumentFile,
        machineId: String,
        database: GalleryDatabase,
        libraryUri: String,
    ) {
        val now = System.currentTimeMillis() / 1000.0
        val cutoff = now - MOVE_RETENTION_SECONDS
        database.pruneSyncMoves(libraryUri, cutoff)
        val root = JSONObject()
            .put("format", SYNC_FORMAT)
            .put("version", SYNC_VERSION)
            .put("machine_id", machineId)
            .put("exported_at", now)
        val media = JSONArray()
        database.syncRecords(libraryUri).forEach { record ->
            val tags = JSONArray()
            record.tags.forEach { tag ->
                tags.put(
                    JSONObject()
                        .put("name", tag.name)
                        .put("updated_at", tag.updatedAt)
                        .put("deleted", tag.deleted),
                )
            }
            media.put(
                JSONObject()
                    .put("path", record.path)
                    .put("size", record.size)
                    .put("tags", tags),
            )
        }
        root.put("media", media)
        val moves = JSONArray()
        database.syncMoves(libraryUri)
            .filter { it.at >= cutoff }
            .forEach { move ->
                moves.put(
                    JSONObject()
                        .put("from", move.from)
                        .put("to", move.to)
                        .put("size", move.size)
                        .put("at", move.at),
                )
            }
        root.put("moves", moves)

        val content = root.toString(1).toByteArray(StandardCharsets.UTF_8)
        if (content.size > MAX_SYNC_BYTES) throw IOException("Gallery sync export exceeds 50 MiB.")
        replaceOwnFile(context, folder, "$SYNC_FILE_PREFIX$machineId.json", content)
    }

    private fun replaceOwnFile(
        context: Context,
        folder: DocumentFile,
        fileName: String,
        contents: ByteArray,
    ) {
        val tempName = "$fileName.${UUID.randomUUID()}.tmp"
        val backupName = "$fileName.${UUID.randomUUID()}.bak"
        val temp = folder.createFile("application/json", tempName)
            ?: throw IOException("The provider could not create the sync export.")
        var backup: DocumentFile? = null
        try {
            context.contentResolver.openOutputStream(temp.uri, "w")?.use { stream ->
                stream.write(contents)
                stream.flush()
            } ?: throw IOException("The provider could not write the sync export.")

            val existing = folder.findFile(fileName)
            if (existing != null) {
                if (!existing.renameTo(backupName)) {
                    throw IOException("The provider could not safely replace the previous sync export.")
                }
                backup = existing
            }
            if (!temp.renameTo(fileName)) {
                backup?.renameTo(fileName)
                throw IOException("The provider could not publish the new sync export.")
            }
            backup?.delete()
        } catch (error: Exception) {
            if (backup != null && folder.findFile(fileName) == null) backup.renameTo(fileName)
            temp.delete()
            throw error
        }
    }

    private fun parentRelativePath(path: String): String =
        path.substringBeforeLast('/', missingDelimiterValue = "")

    internal fun isSafeRelativePath(path: String): Boolean {
        if (path.isBlank() || path.length > 1024 || path.startsWith('/') ||
            '\\' in path || ':' in path || '\u0000' in path
        ) return false
        return path.split('/').none { it.isEmpty() || it == "." || it == ".." }
    }
}
