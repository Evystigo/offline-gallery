package com.evystigo.gallery

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import java.util.Locale
import java.util.UUID

class GalleryDatabase(context: Context) : SQLiteOpenHelper(
    context.applicationContext,
    "gallery.db",
    null,
    2,
) {
    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE media (
                library_uri TEXT NOT NULL,
                relative_path TEXT NOT NULL COLLATE NOCASE,
                size INTEGER NOT NULL,
                last_modified INTEGER NOT NULL,
                is_video INTEGER NOT NULL,
                present INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (library_uri, relative_path)
            )
            """.trimIndent(),
        )
        db.execSQL(
            """
            CREATE TABLE tags (
                library_uri TEXT NOT NULL,
                relative_path TEXT NOT NULL COLLATE NOCASE,
                name TEXT NOT NULL COLLATE NOCASE,
                updated_at REAL NOT NULL DEFAULT 0,
                deleted INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (library_uri, relative_path, name)
            )
            """.trimIndent(),
        )
        createSyncTables(db)
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        if (oldVersion < 2) {
            db.execSQL("ALTER TABLE tags ADD COLUMN updated_at REAL NOT NULL DEFAULT 0")
            db.execSQL("ALTER TABLE tags ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
            db.execSQL(
                "UPDATE tags SET updated_at = ?",
                arrayOf(System.currentTimeMillis() / 1000.0),
            )
            createSyncTables(db)
        }
    }

    fun updateLibrary(libraryUri: String, items: List<MediaItem>) {
        val db = writableDatabase
        db.beginTransaction()
        try {
            db.execSQL("UPDATE media SET present = 0 WHERE library_uri = ?", arrayOf(libraryUri))
            items.forEach { item ->
                val values = ContentValues().apply {
                    put("library_uri", libraryUri)
                    put("relative_path", item.relativePath)
                    put("size", item.size)
                    put("last_modified", item.lastModified)
                    put("is_video", if (item.isVideo) 1 else 0)
                    put("present", 1)
                }
                db.insertWithOnConflict("media", null, values, SQLiteDatabase.CONFLICT_REPLACE)
            }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    fun tagsForPaths(libraryUri: String, paths: List<String>): Map<String, List<String>> {
        if (paths.isEmpty()) return emptyMap()
        val tagsByPath = mutableMapOf<String, MutableList<String>>()
        val db = readableDatabase
        paths.distinctBy { it.lowercase(Locale.ROOT) }.chunked(500).forEach { chunk ->
            val placeholders = List(chunk.size) { "?" }.joinToString(",")
            db.rawQuery(
                "SELECT relative_path, name FROM tags " +
                    "WHERE library_uri = ? AND deleted = 0 AND relative_path IN ($placeholders) " +
                    "ORDER BY name COLLATE NOCASE",
                (listOf(libraryUri) + chunk).toTypedArray(),
            ).use { cursor ->
                val pathColumn = cursor.getColumnIndexOrThrow("relative_path")
                val nameColumn = cursor.getColumnIndexOrThrow("name")
                while (cursor.moveToNext()) {
                    val path = cursor.getString(pathColumn).lowercase(Locale.ROOT)
                    tagsByPath.getOrPut(path) { mutableListOf() }.add(cursor.getString(nameColumn))
                }
            }
        }
        return tagsByPath
    }

    fun allTags(libraryUri: String): List<TagCount> =
        readableDatabase.rawQuery(
            "SELECT name, COUNT(*) FROM tags " +
                "WHERE library_uri = ? AND deleted = 0 GROUP BY name ORDER BY name COLLATE NOCASE",
            arrayOf(libraryUri),
        ).use { cursor ->
            buildList {
                while (cursor.moveToNext()) add(TagCount(cursor.getString(0), cursor.getInt(1)))
            }
        }

    fun renameTag(libraryUri: String, oldName: String, newName: String) {
        val old = normalizeTagName(oldName)
        val new = normalizeTagName(newName)
        require(old.isNotEmpty()) { "Choose a tag to rename." }
        require(new.isNotEmpty()) { "Tag names cannot be empty." }
        if (old.equals(new, ignoreCase = true)) {
            writableDatabase.execSQL(
                "UPDATE tags SET name = ? WHERE library_uri = ? AND name = ?",
                arrayOf(new, libraryUri, old),
            )
            return
        }
        val db = writableDatabase
        val now = currentSyncTime()
        db.beginTransaction()
        try {
            val paths = mutableListOf<String>()
            db.rawQuery(
                "SELECT relative_path FROM tags WHERE library_uri = ? AND name = ? AND deleted = 0",
                arrayOf(libraryUri, old),
            ).use { cursor ->
                while (cursor.moveToNext()) paths.add(cursor.getString(0))
            }
            paths.forEach { path ->
                val added = ContentValues().apply {
                    put("library_uri", libraryUri)
                    put("relative_path", path)
                    put("name", new)
                    put("updated_at", now)
                    put("deleted", 0)
                }
                db.insertWithOnConflict("tags", null, added, SQLiteDatabase.CONFLICT_REPLACE)
                val removed = ContentValues().apply {
                    put("updated_at", now)
                    put("deleted", 1)
                }
                db.update(
                    "tags",
                    removed,
                    "library_uri = ? AND relative_path = ? AND name = ? AND deleted = 0",
                    arrayOf(libraryUri, path, old),
                )
            }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    fun deleteTag(libraryUri: String, name: String) {
        val values = ContentValues().apply {
            put("updated_at", currentSyncTime())
            put("deleted", 1)
        }
        writableDatabase.update(
            "tags",
            values,
            "library_uri = ? AND name = ? AND deleted = 0",
            arrayOf(libraryUri, normalizeTagName(name)),
        )
    }

    fun addTags(libraryUri: String, paths: List<String>, names: List<String>) {
        if (paths.isEmpty() || names.isEmpty()) return
        val db = writableDatabase
        db.beginTransaction()
        try {
            paths.forEach { path ->
                names.forEach { name ->
                    val values = ContentValues().apply {
                        put("library_uri", libraryUri)
                        put("relative_path", path)
                        put("name", name)
                        put("updated_at", currentSyncTime())
                        put("deleted", 0)
                    }
                    db.insertWithOnConflict("tags", null, values, SQLiteDatabase.CONFLICT_REPLACE)
                }
            }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    fun removeTag(libraryUri: String, paths: List<String>, name: String) {
        if (paths.isEmpty()) return
        val db = writableDatabase
        db.beginTransaction()
        try {
            paths.forEach { path ->
                db.delete(
                    "tags",
                    "library_uri = ? AND relative_path = ? AND name = ? AND deleted = 0",
                    arrayOf(libraryUri, path, name),
                )
                val values = ContentValues().apply {
                    put("library_uri", libraryUri)
                    put("relative_path", path)
                    put("name", name)
                    put("updated_at", currentSyncTime())
                    put("deleted", 1)
                }
                db.insertWithOnConflict("tags", null, values, SQLiteDatabase.CONFLICT_REPLACE)
            }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    fun moveTags(
        libraryUri: String,
        oldPath: String,
        newPath: String,
        logMove: Boolean = true,
    ) {
        if (oldPath.equals(newPath, ignoreCase = true)) return
        val db = writableDatabase
        db.beginTransaction()
        try {
            val size = db.rawQuery(
                "SELECT size FROM media WHERE library_uri = ? AND relative_path = ?",
                arrayOf(libraryUri, oldPath),
            ).use { cursor -> if (cursor.moveToFirst()) cursor.getLong(0) else null }
            val tags = mutableListOf<Triple<String, Double, Int>>()
            db.rawQuery(
                "SELECT name, updated_at, deleted FROM tags WHERE library_uri = ? AND relative_path = ?",
                arrayOf(libraryUri, oldPath),
            ).use { cursor ->
                while (cursor.moveToNext()) {
                    tags.add(Triple(cursor.getString(0), cursor.getDouble(1), cursor.getInt(2)))
                }
            }

            tags.forEach { (name, updatedAt, deleted) ->
                val destinationTime = db.rawQuery(
                    "SELECT updated_at FROM tags WHERE library_uri = ? AND relative_path = ? AND name = ?",
                    arrayOf(libraryUri, newPath, name),
                ).use { cursor -> if (cursor.moveToFirst()) cursor.getDouble(0) else null }
                if (destinationTime != null && destinationTime >= updatedAt) return@forEach
                val values = ContentValues().apply {
                    put("library_uri", libraryUri)
                    put("relative_path", newPath)
                    put("name", name)
                    put("updated_at", updatedAt)
                    put("deleted", deleted)
                }
                db.insertWithOnConflict("tags", null, values, SQLiteDatabase.CONFLICT_REPLACE)
            }
            db.delete(
                "tags",
                "library_uri = ? AND relative_path = ?",
                arrayOf(libraryUri, oldPath),
            )
            if (logMove && size != null) {
                val move = ContentValues().apply {
                    put("library_uri", libraryUri)
                    put("from_path", oldPath)
                    put("to_path", newPath)
                    put("size", size)
                    put("moved_at", currentSyncTime())
                }
                db.insertWithOnConflict("move_log", null, move, SQLiteDatabase.CONFLICT_IGNORE)
            }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    fun machineId(): String {
        val db = writableDatabase
        db.rawQuery("SELECT value FROM settings WHERE key = 'machine_id'", null).use { cursor ->
            if (cursor.moveToFirst()) return cursor.getString(0)
        }
        val id = UUID.randomUUID().toString().replace("-", "").take(12)
        val values = ContentValues().apply {
            put("key", "machine_id")
            put("value", id)
        }
        db.insertWithOnConflict("settings", null, values, SQLiteDatabase.CONFLICT_IGNORE)
        return machineId()
    }

    fun syncRecords(libraryUri: String): List<SyncMediaRecord> {
        val records = linkedMapOf<String, SyncMediaRecord>()
        readableDatabase.rawQuery(
            "SELECT m.relative_path, m.size, t.name, t.updated_at, t.deleted " +
                "FROM media m JOIN tags t ON t.library_uri = m.library_uri " +
                "AND t.relative_path = m.relative_path " +
                "WHERE m.library_uri = ? AND m.present = 1 ORDER BY m.relative_path, t.name",
            arrayOf(libraryUri),
        ).use { cursor ->
            while (cursor.moveToNext()) {
                val path = cursor.getString(0)
                val record = records.getOrPut(path) {
                    SyncMediaRecord(path, cursor.getLong(1), mutableListOf())
                }
                record.tags.add(
                    SyncTagRecord(
                        name = cursor.getString(2),
                        updatedAt = cursor.getDouble(3),
                        deleted = cursor.getInt(4) != 0,
                    ),
                )
            }
        }
        return records.values.toList()
    }

    fun pendingTags(libraryUri: String): List<PendingSyncTag> =
        readableDatabase.rawQuery(
            "SELECT relative_path, name, updated_at, deleted FROM pending_sync WHERE library_uri = ?",
            arrayOf(libraryUri),
        ).use { cursor ->
            buildList {
                while (cursor.moveToNext()) {
                    add(
                        PendingSyncTag(
                            path = cursor.getString(0),
                            name = cursor.getString(1),
                            updatedAt = cursor.getDouble(2),
                            deleted = cursor.getInt(3) != 0,
                        ),
                    )
                }
            }
        }

    fun recordMove(libraryUri: String, from: String, to: String, size: Long, at: Double = currentSyncTime()) {
        val values = ContentValues().apply {
            put("library_uri", libraryUri)
            put("from_path", from)
            put("to_path", to)
            put("size", size)
            put("moved_at", at)
        }
        writableDatabase.insertWithOnConflict(
            "move_log",
            null,
            values,
            SQLiteDatabase.CONFLICT_IGNORE,
        )
    }

    fun syncMoves(libraryUri: String): List<SyncMoveRecord> =
        readableDatabase.rawQuery(
            "SELECT from_path, to_path, size, moved_at FROM move_log " +
                "WHERE library_uri = ? ORDER BY moved_at, from_path",
            arrayOf(libraryUri),
        ).use { cursor ->
            buildList {
                while (cursor.moveToNext()) {
                    add(SyncMoveRecord(cursor.getString(0), cursor.getString(1), cursor.getLong(2), cursor.getDouble(3)))
                }
            }
        }

    fun pruneSyncMoves(libraryUri: String, cutoff: Double) {
        writableDatabase.delete(
            "move_log",
            "library_uri = ? AND moved_at < ?",
            arrayOf(libraryUri, cutoff.toString()),
        )
    }

    fun mergeSyncTag(
        libraryUri: String,
        path: String,
        name: String,
        updatedAt: Double,
        deleted: Boolean,
    ): Boolean {
        val db = writableDatabase
        db.beginTransaction()
        try {
            val current = mutableListOf<Pair<Double, Boolean>>()
            listOf("tags", "pending_sync").forEach { table ->
                db.rawQuery(
                    "SELECT updated_at, deleted FROM $table " +
                        "WHERE library_uri = ? AND relative_path = ? AND name = ?",
                    arrayOf(libraryUri, path, name),
                ).use { cursor ->
                    if (cursor.moveToFirst()) current.add(cursor.getDouble(0) to (cursor.getInt(1) != 0))
                }
            }
            val latest = current.maxByOrNull { it.first }
            if (latest != null && updatedAt <= latest.first) {
                db.setTransactionSuccessful()
                return false
            }
            val changed = latest == null && !deleted || latest != null && latest.second != deleted
            val existingMedia = db.rawQuery(
                "SELECT present FROM media WHERE library_uri = ? AND relative_path = ?",
                arrayOf(libraryUri, path),
            ).use { cursor -> cursor.moveToFirst() && cursor.getInt(0) != 0 }
            if (existingMedia) {
                val values = ContentValues().apply {
                    put("library_uri", libraryUri)
                    put("relative_path", path)
                    put("name", name)
                    put("updated_at", updatedAt)
                    put("deleted", if (deleted) 1 else 0)
                }
                db.insertWithOnConflict("tags", null, values, SQLiteDatabase.CONFLICT_REPLACE)
            } else {
                val values = ContentValues().apply {
                    put("library_uri", libraryUri)
                    put("relative_path", path)
                    put("name", name)
                    put("updated_at", updatedAt)
                    put("deleted", if (deleted) 1 else 0)
                }
                db.insertWithOnConflict("pending_sync", null, values, SQLiteDatabase.CONFLICT_REPLACE)
            }
            db.setTransactionSuccessful()
            return changed
        } finally {
            db.endTransaction()
        }
    }

    fun attachPending(libraryUri: String): Int {
        val records = pendingTags(libraryUri)
        var attached = 0
        records.forEach { record ->
            val exists = readableDatabase.rawQuery(
                "SELECT 1 FROM media WHERE library_uri = ? AND relative_path = ? AND present = 1",
                arrayOf(libraryUri, record.path),
            ).use { it.moveToFirst() }
            if (exists) {
                if (mergeSyncTag(libraryUri, record.path, record.name, record.updatedAt, record.deleted)) {
                    attached++
                }
                writableDatabase.delete(
                    "pending_sync",
                    "library_uri = ? AND relative_path = ? AND name = ?",
                    arrayOf(libraryUri, record.path, record.name),
                )
            }
        }
        return attached
    }

    fun knownMove(libraryUri: String, move: SyncMoveRecord): Boolean =
        readableDatabase.rawQuery(
            "SELECT 1 FROM move_log WHERE library_uri = ? AND from_path = ? AND to_path = ? " +
                "AND size = ? AND moved_at = ?",
            arrayOf(libraryUri, move.from, move.to, move.size.toString(), move.at.toString()),
        ).use { it.moveToFirst() }

    private fun createSyncTables(db: SQLiteDatabase) {
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS pending_sync (
                library_uri TEXT NOT NULL,
                relative_path TEXT NOT NULL COLLATE NOCASE,
                name TEXT NOT NULL COLLATE NOCASE,
                updated_at REAL NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (library_uri, relative_path, name)
            )
            """.trimIndent(),
        )
        db.execSQL(
            """
            CREATE TABLE IF NOT EXISTS move_log (
                library_uri TEXT NOT NULL,
                from_path TEXT NOT NULL,
                to_path TEXT NOT NULL,
                size INTEGER NOT NULL,
                moved_at REAL NOT NULL,
                PRIMARY KEY (library_uri, from_path, to_path, size, moved_at)
            )
            """.trimIndent(),
        )
        db.execSQL("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    }
}

private fun currentSyncTime(): Double = System.currentTimeMillis() / 1000.0

data class SyncTagRecord(val name: String, val updatedAt: Double, val deleted: Boolean)
data class SyncMediaRecord(val path: String, val size: Long, val tags: MutableList<SyncTagRecord>)
data class PendingSyncTag(val path: String, val name: String, val updatedAt: Double, val deleted: Boolean)
data class SyncMoveRecord(val from: String, val to: String, val size: Long, val at: Double)
data class TagCount(val name: String, val count: Int)

private fun normalizeTagName(name: String): String =
    name.trim().split(Regex("\\s+")).filter(String::isNotEmpty).joinToString(" ")

fun parseTagNames(text: String): List<String> = text
    .split(',')
    .map { it.trim().split(Regex("\\s+")).filter(String::isNotEmpty).joinToString(" ") }
    .filter(String::isNotEmpty)
    .distinctBy { it.lowercase(Locale.ROOT) }
