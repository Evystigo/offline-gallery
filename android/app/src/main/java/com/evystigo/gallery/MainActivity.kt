package com.evystigo.gallery

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.database.sqlite.SQLiteException
import android.net.Uri
import android.os.Bundle
import android.provider.OpenableColumns
import android.provider.DocumentsContract
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items as rowItems
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import coil.compose.AsyncImage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.IOException
import java.util.Locale

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            val preferences = remember {
                getSharedPreferences("gallery", Context.MODE_PRIVATE)
            }
            val systemDarkMode = isSystemInDarkTheme()
            var darkMode by remember {
                mutableStateOf(preferences.getBoolean("dark_mode", systemDarkMode))
            }
            MaterialTheme(
                colorScheme = if (darkMode) darkColorScheme() else lightColorScheme(),
            ) {
                GalleryApp(
                    darkMode = darkMode,
                    onDarkModeChange = { enabled ->
                        darkMode = enabled
                        preferences.edit().putBoolean("dark_mode", enabled).apply()
                    },
                )
            }
        }
    }
}

@Composable
private fun GalleryApp(
    darkMode: Boolean,
    onDarkModeChange: (Boolean) -> Unit,
) {
    val context = LocalContext.current
    val preferences = remember {
        context.getSharedPreferences("gallery", Context.MODE_PRIVATE)
    }
    val database = remember { GalleryDatabase(context) }
    DisposableEffect(database) {
        onDispose { database.close() }
    }
    val initialUri = remember {
        preferences.getString("library_tree_uri", null)?.let(Uri::parse)
    }
    val initialSyncUri = remember {
        preferences.getString("sync_tree_uri", null)?.let(Uri::parse)
    }
    var treeUri by remember { mutableStateOf(initialUri) }
    var syncFolderUri by remember { mutableStateOf(initialSyncUri) }
    var applySyncedMoves by remember {
        mutableStateOf(preferences.getBoolean("apply_sync_moves", true))
    }
    var media by remember { mutableStateOf<List<MediaItem>>(emptyList()) }
    var folders by remember { mutableStateOf<List<String>>(emptyList()) }
    var folderUris by remember { mutableStateOf<Map<String, Uri>>(emptyMap()) }
    var currentAlbum by remember { mutableStateOf("") }
    var isScanning by remember { mutableStateOf(false) }
    var hasWriteAccess by remember { mutableStateOf(false) }
    var searchText by remember { mutableStateOf("") }
    var message by remember { mutableStateOf<String?>(null) }
    var selectedItem by remember { mutableStateOf<MediaItem?>(null) }
    var selectionMode by remember { mutableStateOf(false) }
    var selectedPaths by remember { mutableStateOf(emptySet<String>()) }
    var tagDialogPaths by remember { mutableStateOf<List<String>?>(null) }
    var tagManagerOpen by remember { mutableStateOf(false) }
    var selectedManagedTag by remember { mutableStateOf<String?>(null) }
    var tagRenameText by remember { mutableStateOf("") }
    var tagDeleteConfirmation by remember { mutableStateOf<String?>(null) }
    var tagChangeRunning by remember { mutableStateOf(false) }
    var moveDialogOpen by remember { mutableStateOf(false) }
    var renameDialogOpen by remember { mutableStateOf(false) }
    var renamePrefix by remember { mutableStateOf("") }
    var createAlbumDialogOpen by remember { mutableStateOf(false) }
    var newAlbumName by remember { mutableStateOf("") }
    var duplicateScanRunning by remember { mutableStateOf(false) }
    var duplicateResult by remember { mutableStateOf<DuplicateScanResult?>(null) }
    var similarPhotoScanRunning by remember { mutableStateOf(false) }
    var similarPhotoResult by remember { mutableStateOf<SimilarPhotoScanResult?>(null) }
    var syncRunning by remember { mutableStateOf(false) }
    var requiresFolderReselection by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    suspend fun scanLibrary(selectedFolder: Uri) {
        isScanning = true
        message = null
        selectedPaths = emptySet()
        selectionMode = false
        try {
            val scan = withContext(Dispatchers.IO) {
                val scanned = MediaScanner.scan(context, selectedFolder)
                val libraryKey = selectedFolder.toString()
                database.updateLibrary(libraryKey, scanned.media)
                database.attachPending(libraryKey)
                val tags = database.tagsForPaths(libraryKey, scanned.media.map { it.relativePath })
                scanned.copy(media = scanned.media.map { item ->
                    item.copy(tags = tags[item.relativePath.lowercase(Locale.ROOT)] ?: emptyList())
                })
            }
            media = scan.media
            folders = scan.folders
            folderUris = scan.folderUris
            hasWriteAccess = context.contentResolver.persistedUriPermissions.any {
                it.uri == selectedFolder && it.isWritePermission
            }
            if (currentAlbum.isNotEmpty() && folders.none {
                    it.equals(currentAlbum, ignoreCase = true) ||
                        it.startsWith("$currentAlbum/", ignoreCase = true)
                }
            ) {
                currentAlbum = ""
            }
            requiresFolderReselection = false
            selectedPaths = emptySet()
            selectionMode = false
        } catch (_: SecurityException) {
            media = emptyList()
            folders = emptyList()
            folderUris = emptyMap()
            currentAlbum = ""
            message = "Folder access was lost. Select the library folder again."
            requiresFolderReselection = true
        } catch (error: java.io.IOException) {
            media = emptyList()
            folders = emptyList()
            folderUris = emptyMap()
            currentAlbum = ""
            message = error.message ?: "Gallery couldn't read the selected folder."
            requiresFolderReselection = false
        } catch (error: SQLiteException) {
            message = "Couldn't load the local library database: ${error.localizedMessage ?: "database error"}"
            requiresFolderReselection = false
        } finally {
            isScanning = false
        }
    }

    fun changeTags(paths: List<String>, names: List<String>, remove: Boolean = false) {
        val libraryKey = treeUri?.toString() ?: return
        scope.launch {
            try {
                val tagsByPath = withContext(Dispatchers.IO) {
                    if (remove) {
                        database.removeTag(libraryKey, paths, names.single())
                    } else {
                        database.addTags(libraryKey, paths, names)
                    }
                    database.tagsForPaths(libraryKey, paths)
                }
                val pathKeys = paths.map { it.lowercase(Locale.ROOT) }.toSet()
                media = media.map { item ->
                    if (item.relativePath.lowercase(Locale.ROOT) in pathKeys) {
                        item.copy(tags = tagsByPath[item.relativePath.lowercase(Locale.ROOT)] ?: emptyList())
                    } else {
                        item
                    }
                }
                message = null
            } catch (error: SQLiteException) {
                message = "Couldn't save tags: ${error.localizedMessage ?: "database error"}"
            }
        }
    }

    fun renameLibraryTag(oldName: String, newName: String) {
        val libraryKey = treeUri?.toString() ?: return
        val normalizedName = parseTagNames(newName).singleOrNull()
        if (normalizedName == null) {
            message = "Enter one non-empty tag name without commas."
            return
        }
        scope.launch {
            tagChangeRunning = true
            try {
                val tagsByPath = withContext(Dispatchers.IO) {
                    database.renameTag(libraryKey, oldName, normalizedName)
                    database.tagsForPaths(libraryKey, media.map { it.relativePath })
                }
                media = media.map { item ->
                    item.copy(tags = tagsByPath[item.relativePath.lowercase(Locale.ROOT)] ?: emptyList())
                }
                selectedManagedTag = null
                message = "Tag renamed."
            } catch (error: SQLiteException) {
                message = "Couldn't rename the tag: ${error.localizedMessage ?: "database error"}"
            } catch (error: IllegalArgumentException) {
                message = error.localizedMessage ?: "Invalid tag name."
            } finally {
                tagChangeRunning = false
            }
        }
    }

    fun deleteLibraryTag(name: String) {
        val libraryKey = treeUri?.toString() ?: return
        scope.launch {
            tagChangeRunning = true
            try {
                val tagsByPath = withContext(Dispatchers.IO) {
                    database.deleteTag(libraryKey, name)
                    database.tagsForPaths(libraryKey, media.map { it.relativePath })
                }
                media = media.map { item ->
                    item.copy(tags = tagsByPath[item.relativePath.lowercase(Locale.ROOT)] ?: emptyList())
                }
                selectedManagedTag = null
                message = "Tag deleted from the library."
            } catch (error: SQLiteException) {
                message = "Couldn't delete the tag: ${error.localizedMessage ?: "database error"}"
            } finally {
                tagChangeRunning = false
            }
        }
    }

    fun selectFolderPhotos(folder: String) {
        val photos = media.asSequence()
            .filter { isPhotoInAlbum(it.relativePath, it.isVideo, folder) }
            .map { it.relativePath }
            .toSet()
        if (photos.isEmpty()) {
            message = "There are no photos in this folder or its subfolders."
            return
        }
        val selectedPhotos = media.asSequence()
            .filterNot { it.isVideo }
            .map { it.relativePath }
            .filter { it in selectedPaths }
            .toSet()
        selectedPaths = selectedPhotos + photos
        selectionMode = true
        message = "Selected ${photos.size} photo(s) in this folder and its subfolders " +
            "(${selectedPaths.size} photo(s) selected total). Choose Edit tags to apply tags."
    }

    fun navigateAlbum(path: String) {
        currentAlbum = path
        selectedPaths = emptySet()
        selectionMode = false
    }

    fun createAlbum(rawName: String) {
        val libraryUri = treeUri ?: return
        val name = rawName.trim()
        val nameError = validateAlbumName(name, folders, currentAlbum)
        if (nameError != null) {
            message = nameError
            return
        }
        val parentUri = folderUris[currentAlbum]
        if (parentUri == null) {
            message = "The current folder is unavailable. Refresh the library and try again."
            createAlbumDialogOpen = false
            return
        }

        createAlbumDialogOpen = false
        scope.launch {
            isScanning = true
            try {
                val created = withContext(Dispatchers.IO) {
                    DocumentsContract.createDocument(
                        context.contentResolver,
                        parentUri,
                        DocumentsContract.Document.MIME_TYPE_DIR,
                        name,
                    )
                }
                if (created == null) {
                    message = "The selected storage provider did not create the album."
                    return@launch
                }
                val newPath = if (currentAlbum.isEmpty()) name else "$currentAlbum/$name"
                currentAlbum = newPath
                newAlbumName = ""
                scanLibrary(libraryUri)
                message = "Album created."
            } catch (error: kotlinx.coroutines.CancellationException) {
                throw error
            } catch (error: Exception) {
                message = "Couldn't create album: ${error.localizedMessage ?: "the storage provider may not support creating folders."}"
            } finally {
                isScanning = false
            }
        }
    }

    fun moveSelectedTo(destination: String) {
        val libraryUri = treeUri ?: return
        val destinationUri = folderUris[destination]
        if (destinationUri == null) {
            message = "The destination folder is no longer available. Refresh the library."
            return
        }
        val selectedItems = media.filter { it.relativePath in selectedPaths }
        moveDialogOpen = false
        scope.launch {
            isScanning = true
            val failures = mutableListOf<String>()
            var moved = 0
            var alreadyThere = 0
            try {
                withContext(Dispatchers.IO) {
                    selectedItems.forEach { item ->
                        if (parentAlbum(item.relativePath).equals(destination, ignoreCase = true)) {
                            alreadyThere++
                            return@forEach
                        }
                        try {
                            val result = DocumentsContract.moveDocument(
                                context.contentResolver,
                                item.uri,
                                item.parentUri,
                                destinationUri,
                            )
                            if (result == null) {
                                throw java.io.IOException("The folder provider did not complete the move.")
                            }
                            moved++
                            try {
                                database.moveTags(
                                    libraryUri.toString(),
                                    item.relativePath,
                                    if (destination.isEmpty()) item.name else "$destination/${item.name}",
                                )
                            } catch (error: SQLiteException) {
                                failures.add("${item.name}: file moved, but its local tags couldn't be updated.")
                            }
                        } catch (error: kotlinx.coroutines.CancellationException) {
                            throw error
                        } catch (error: Exception) {
                            failures.add(
                                "${item.name}: ${error.localizedMessage ?: "the folder provider couldn't move this file."}",
                            )
                        }
                    }
                }
            } finally {
                isScanning = false
            }

            val summary = buildList {
                add("$moved moved")
                if (alreadyThere > 0) add("$alreadyThere already in that album")
                if (failures.isNotEmpty()) add("${failures.size} couldn't be moved")
                if (failures.isNotEmpty()) add(failures.take(3).joinToString(" "))
            }.joinToString(". ")
            message = summary
            treeUri?.let { scanLibrary(it) }
            message = summary
        }
    }

    fun renameSelected(prefix: String) {
        val libraryUri = treeUri ?: return
        val selectedItems = media.filter { it.relativePath in selectedPaths }
        val validationError = validateRenamePrefix(prefix, selectedItems, media)
        if (validationError != null) {
            message = validationError
            return
        }

        renameDialogOpen = false
        scope.launch {
            isScanning = true
            val failures = mutableListOf<String>()
            var renamed = 0
            var unchanged = 0
            try {
                withContext(Dispatchers.IO) {
                    selectedItems.sortedBy { it.relativePath.lowercase(Locale.ROOT) }
                        .forEachIndexed { index, item ->
                            val extension = fileExtension(item.name)
                            val newName = "$prefix${index + 1}$extension"
                            if (newName.equals(item.name, ignoreCase = true)) {
                                unchanged++
                                return@forEachIndexed
                            }
                            try {
                                val result = DocumentsContract.renameDocument(
                                    context.contentResolver,
                                    item.uri,
                                    newName,
                                ) ?: throw java.io.IOException("The folder provider did not complete the rename.")
                                val actualName = queryDisplayName(context, result)
                                    ?: throw java.io.IOException("The folder provider renamed the file but did not report its new name.")
                                renamed++
                                val parent = parentAlbum(item.relativePath)
                                val newPath = if (parent.isEmpty()) actualName else "$parent/$actualName"
                                database.moveTags(libraryUri.toString(), item.relativePath, newPath)
                                if (!actualName.equals(newName, ignoreCase = true)) {
                                    failures.add("$newName was saved as $actualName by the folder provider.")
                                }
                            } catch (error: kotlinx.coroutines.CancellationException) {
                                throw error
                            } catch (error: Exception) {
                                failures.add(
                                    "${item.name}: ${error.localizedMessage ?: "the folder provider couldn't rename this file."}",
                                )
                            }
                        }
                }
            } finally {
                isScanning = false
            }

            val summary = buildList {
                add("$renamed renamed")
                if (unchanged > 0) add("$unchanged unchanged")
                if (failures.isNotEmpty()) add("${failures.size} issue(s)")
                if (failures.isNotEmpty()) add(failures.take(3).joinToString(" "))
            }.joinToString(". ")
            treeUri?.let { scanLibrary(it) }
            message = summary
        }
    }

    fun findDuplicates() {
        if (duplicateScanRunning || similarPhotoScanRunning || isScanning) return
        duplicateResult = null
        duplicateScanRunning = true
        val itemsToScan = media.toList()
        scope.launch {
            try {
                duplicateResult = withContext(Dispatchers.IO) {
                    DuplicateScanner.findExactDuplicates(context, itemsToScan)
                }
            } catch (error: kotlinx.coroutines.CancellationException) {
                throw error
            } catch (error: SecurityException) {
                message = "Couldn't scan duplicates because folder access was denied: ${error.localizedMessage ?: "access denied"}"
            } catch (error: IOException) {
                message = "Couldn't scan duplicates: ${error.localizedMessage ?: "file access failed"}"
            } finally {
                duplicateScanRunning = false
            }
        }
    }

    fun findSimilarPhotos() {
        if (duplicateScanRunning || similarPhotoScanRunning || isScanning) return
        similarPhotoResult = null
        similarPhotoScanRunning = true
        val itemsToScan = media.toList()
        scope.launch {
            try {
                similarPhotoResult = withContext(Dispatchers.IO) {
                    SimilarPhotoScanner.findSimilarPhotos(context, itemsToScan)
                }
            } catch (error: kotlinx.coroutines.CancellationException) {
                throw error
            } catch (error: SecurityException) {
                message = "Couldn't scan similar photos because folder access was denied: ${error.localizedMessage ?: "access denied"}"
            } catch (error: IOException) {
                message = "Couldn't scan similar photos: ${error.localizedMessage ?: "file access failed"}"
            } finally {
                similarPhotoScanRunning = false
            }
        }
    }

    fun syncLibrary() {
        val libraryUri = treeUri ?: return
        val selectedSyncFolder = syncFolderUri ?: run {
            message = "Choose a sync folder first."
            return
        }
        if (syncRunning || isScanning) return
        syncRunning = true
        message = null
        scope.launch {
            try {
                val result = withContext(Dispatchers.IO) {
                    GallerySync.sync(
                        context = context,
                        database = database,
                        libraryUri = libraryUri,
                        syncFolderUri = selectedSyncFolder,
                        applyMoves = applySyncedMoves,
                    )
                }
                scanLibrary(libraryUri)
                message = buildList {
                    add("Sync complete: ${result.filesRead} device file(s) read, ${result.tagsChanged} tag change(s)")
                    add("${result.movesApplied} move(s) applied")
                    if (result.recordsHeld > 0) add("${result.recordsHeld} record(s) waiting for files")
                    if (result.movesSkipped.isNotEmpty()) {
                        add("${result.movesSkipped.size} move(s) skipped")
                        add(result.movesSkipped.take(2).joinToString("; "))
                    }
                    if (result.errors.isNotEmpty()) {
                        add("${result.errors.size} sync file(s) had issues")
                        add(result.errors.take(2).joinToString("; "))
                    }
                }.joinToString(". ")
            } catch (error: kotlinx.coroutines.CancellationException) {
                throw error
            } catch (error: SecurityException) {
                message = "Sync access was denied: ${error.localizedMessage ?: "permission denied"}"
            } catch (error: IOException) {
                message = "Couldn't sync: ${error.localizedMessage ?: "storage error"}"
            } catch (error: SQLiteException) {
                message = "Couldn't update local sync data: ${error.localizedMessage ?: "database error"}"
            } finally {
                syncRunning = false
            }
        }
    }

    val folderPicker = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.OpenDocumentTree(),
    ) { selectedUri ->
        if (selectedUri != null) {
            try {
                context.contentResolver.takePersistableUriPermission(
                    selectedUri,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION,
                )
                val writeGranted = try {
                    context.contentResolver.takePersistableUriPermission(
                        selectedUri,
                        Intent.FLAG_GRANT_WRITE_URI_PERMISSION,
                    )
                    true
                } catch (_: SecurityException) {
                    false
                }
                preferences.edit().putString("library_tree_uri", selectedUri.toString()).apply()
                treeUri = selectedUri
                currentAlbum = ""
                folders = emptyList()
                media = emptyList()
                selectedPaths = emptySet()
                selectionMode = false
                tagDialogPaths = null
                message = null
                requiresFolderReselection = false
                if (!writeGranted) {
                    message = "This folder is read-only. You can browse and tag, but moving files requires write access."
                }
            } catch (error: SecurityException) {
                message = "Gallery couldn't keep access to that folder: ${error.localizedMessage ?: "permission denied"}"
                requiresFolderReselection = true
            }
        }
    }

    val syncFolderPicker = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.OpenDocumentTree(),
    ) { selectedUri ->
        if (selectedUri != null) {
            try {
                context.contentResolver.takePersistableUriPermission(
                    selectedUri,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION,
                )
                val hasReadAndWrite = context.contentResolver.persistedUriPermissions.any {
                    it.uri == selectedUri && it.isReadPermission && it.isWritePermission
                }
                if (!hasReadAndWrite) {
                    message = "Sync requires read and write access to the selected folder."
                } else {
                    preferences.edit().putString("sync_tree_uri", selectedUri.toString()).apply()
                    syncFolderUri = selectedUri
                    message = "Sync folder selected. Run Sync now to exchange changes."
                }
            } catch (error: SecurityException) {
                message = "Gallery couldn't keep access to the sync folder: ${error.localizedMessage ?: "permission denied"}"
            }
        }
    }

    LaunchedEffect(treeUri) {
        val selectedFolder = treeUri ?: return@LaunchedEffect
        scanLibrary(selectedFolder)
    }

    val albumContents = remember(media, folders, currentAlbum) {
        val prefix = if (currentAlbum.isEmpty()) "" else "$currentAlbum/"
        val children = folders.filter { folder ->
            folder.startsWith(prefix, ignoreCase = true) &&
                folder.substring(prefix.length).isNotEmpty() &&
                '/' !in folder.substring(prefix.length)
        }
        val albumMedia = media.filter { albumContainsMedia(parentAlbum(it.relativePath), currentAlbum) }
        children to albumMedia
    }
    val searchQuery = remember(searchText) { parseGallerySearch(searchText) }
    val libraryTags = remember(media) {
        media.flatMap { it.tags }
            .groupBy { it.lowercase(Locale.ROOT) }
            .map { (_, names) -> names.first() to names.size }
            .sortedBy { it.first.lowercase(Locale.ROOT) }
            .map { TagCount(it.first, it.second) }
    }
    val mediaToFilter = if (searchText.isBlank()) albumContents.second else media
    val filteredMedia = remember(mediaToFilter, searchQuery) {
        mediaToFilter.filter { searchQuery.matches(it.toSearchDocument()) }
    }
    val previewPhotos = remember(filteredMedia) { filteredMedia.filterNot { it.isVideo } }

    Scaffold { innerPadding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
                .padding(horizontal = 16.dp),
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 12.dp, bottom = 8.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text("Gallery", style = MaterialTheme.typography.headlineSmall)
                    Text(
                        if (treeUri == null) "Choose a library folder to get started"
                        else "${media.size} items",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
                TextButton(onClick = { folderPicker.launch(treeUri) }) {
                    Text(if (treeUri == null) "Choose folder" else "Change folder")
                }
            }
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.End,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("Dark mode", style = MaterialTheme.typography.bodySmall)
                Switch(
                    checked = darkMode,
                    onCheckedChange = onDarkModeChange,
                )
            }

            if (treeUri != null) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(bottom = 4.dp),
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            if (syncFolderUri == null) "Sync is off" else "Sync folder selected",
                            modifier = Modifier.weight(1f),
                            style = MaterialTheme.typography.bodySmall,
                        )
                        TextButton(
                            enabled = !syncRunning && !isScanning,
                            onClick = { syncFolderPicker.launch(syncFolderUri) },
                        ) {
                            Text(if (syncFolderUri == null) "Choose sync folder" else "Change")
                        }
                        if (syncFolderUri != null) {
                            TextButton(
                                enabled = !syncRunning && !isScanning,
                                onClick = { syncLibrary() },
                            ) {
                                Text("Sync now")
                            }
                        }
                    }
                    if (syncFolderUri != null) {
                        Text(
                            "Sync shares tags and relative paths only. Run it on each device; media files are not uploaded by Gallery.",
                            modifier = Modifier.padding(start = 8.dp),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.secondary,
                        )
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Switch(
                                checked = applySyncedMoves,
                                enabled = !syncRunning && !isScanning,
                                onCheckedChange = { enabled ->
                                    applySyncedMoves = enabled
                                    preferences.edit().putBoolean("apply_sync_moves", enabled).apply()
                                },
                            )
                            Text(
                                "Apply album moves from other devices",
                                style = MaterialTheme.typography.bodySmall,
                            )
                            TextButton(
                                enabled = !syncRunning && !isScanning,
                                onClick = {
                                    preferences.edit().remove("sync_tree_uri").apply()
                                    syncFolderUri?.let { oldUri ->
                                        try {
                                            context.contentResolver.releasePersistableUriPermission(
                                                oldUri,
                                                Intent.FLAG_GRANT_READ_URI_PERMISSION or
                                                    Intent.FLAG_GRANT_WRITE_URI_PERMISSION,
                                            )
                                        } catch (_: SecurityException) {
                                            message = "Sync was turned off, but Android couldn't release the old folder permission."
                                        }
                                    }
                                    syncFolderUri = null
                                },
                            ) {
                                Text("Turn off")
                            }
                        }
                    }
                }
            }

            if (treeUri == null) {
                Column(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(bottom = 64.dp),
                    verticalArrangement = Arrangement.Center,
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Text(
                        "Your library stays on your device. Choose a folder to browse photos and videos in it and its subfolders.",
                        style = MaterialTheme.typography.bodyLarge,
                    )
                    Button(
                        modifier = Modifier.padding(top = 16.dp),
                        onClick = { folderPicker.launch(null) },
                    ) {
                        Text("Choose library folder")
                    }
                }
            } else {
                if (!isScanning && !hasWriteAccess) {
                    Text(
                        "Read-only folder: browsing and tagging work, but file moves are unavailable.",
                        modifier = Modifier.padding(vertical = 4.dp),
                        color = MaterialTheme.colorScheme.secondary,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    TextButton(
                        enabled = !isScanning,
                        onClick = {
                            treeUri?.let { selectedFolder ->
                                scope.launch { scanLibrary(selectedFolder) }
                            }
                        },
                    ) {
                        Text("Refresh")
                    }
                    if (selectionMode) {
                        Text("${selectedPaths.size} selected", modifier = Modifier.weight(1f))
                        TextButton(
                            enabled = selectedPaths.isNotEmpty() && !isScanning,
                            onClick = {
                                if (hasWriteAccess) {
                                    moveDialogOpen = true
                                } else {
                                    message = "Moving files requires folder write access. Select this folder again and grant write access."
                                }
                            },
                        ) {
                            Text("Move")
                        }
                        TextButton(
                            enabled = selectedPaths.isNotEmpty() && !isScanning,
                            onClick = {
                                if (hasWriteAccess) {
                                    renamePrefix = ""
                                    renameDialogOpen = true
                                } else {
                                    message = "Renaming files requires folder write access. Select this folder again and grant write access."
                                }
                            },
                        ) {
                            Text("Rename")
                        }
                        TextButton(
                            enabled = selectedPaths.isNotEmpty() && !isScanning,
                            onClick = { tagDialogPaths = selectedPaths.toList() },
                        ) {
                            Text("Edit tags")
                        }
                        TextButton(
                            onClick = {
                                selectionMode = false
                                selectedPaths = emptySet()
                            },
                        ) {
                            Text("Cancel")
                        }
                    } else {
                        TextButton(
                            enabled = !isScanning,
                            onClick = { selectionMode = true },
                        ) {
                            Text("Select items to tag")
                        }
                    }
                    if (!selectionMode && hasWriteAccess) {
                        TextButton(
                            enabled = !isScanning,
                            onClick = {
                                newAlbumName = ""
                                createAlbumDialogOpen = true
                            },
                        ) {
                            Text("New album")
                        }
                    }
                }

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.End,
                ) {
                    TextButton(
                        enabled = !isScanning && !tagChangeRunning,
                        onClick = {
                            selectedManagedTag = null
                            tagRenameText = ""
                            tagManagerOpen = true
                        },
                    ) {
                        Text("Manage tags")
                    }
                }

                Column(modifier = Modifier.fillMaxWidth()) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        TextButton(
                            modifier = Modifier.weight(1f),
                            enabled = !isScanning && !duplicateScanRunning &&
                                !similarPhotoScanRunning && media.isNotEmpty(),
                            onClick = { findDuplicates() },
                        ) {
                            Text("Exact duplicates")
                        }
                        TextButton(
                            modifier = Modifier.weight(1f),
                            enabled = !isScanning && !duplicateScanRunning &&
                                !similarPhotoScanRunning && media.isNotEmpty(),
                            onClick = { findSimilarPhotos() },
                        ) {
                            Text("Similar media")
                        }
                    }
                    if (duplicateScanRunning || similarPhotoScanRunning) {
                        Row(
                            modifier = Modifier.padding(bottom = 8.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            CircularProgressIndicator(modifier = Modifier.padding(end = 8.dp))
                            Text(
                                if (duplicateScanRunning) "Checking files…" else "Comparing media…",
                                style = MaterialTheme.typography.bodySmall,
                            )
                        }
                    }
                }

                OutlinedTextField(
                    value = searchText,
                    onValueChange = { searchText = it },
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = 8.dp),
                    label = {
                        Text("Search tags, names, album:, type:, after:, before:")
                    },
                    singleLine = true,
                    isError = searchQuery.error != null,
                    supportingText = searchQuery.error?.let { error -> { Text(error) } },
                )

                if (currentAlbum.isNotEmpty()) {
                    LazyRow(
                        horizontalArrangement = Arrangement.spacedBy(4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        item {
                            TextButton(onClick = { navigateAlbum("") }) {
                                Text("All media")
                            }
                        }
                        val segments = currentAlbum.split('/')
                        segments.indices.forEach { index ->
                            val path = segments.take(index + 1).joinToString("/")
                            item {
                                Text("›")
                                TextButton(onClick = { navigateAlbum(path) }) {
                                    Text(segments[index])
                                }
                            }
                        }
                    }
                }

                when {
                    isScanning || syncRunning -> Box(
                        modifier = Modifier.fillMaxSize(),
                        contentAlignment = Alignment.Center,
                    ) {
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            CircularProgressIndicator()
                            Text(
                                if (syncRunning) "Syncing with other devices…" else "Scanning library…",
                                modifier = Modifier.padding(top = 12.dp),
                            )
                        }
                    }

                    else -> {
                        message?.let { error ->
                            Text(
                                error,
                                modifier = Modifier.padding(vertical = 8.dp),
                                color = MaterialTheme.colorScheme.error,
                            )
                            TextButton(
                                onClick = {
                                    message = null
                                    if (requiresFolderReselection) {
                                        folderPicker.launch(treeUri)
                                    }
                                },
                            ) {
                                Text(if (requiresFolderReselection) "Choose folder again" else "Dismiss")
                            }
                        }

                        if (albumContents.first.isNotEmpty()) {
                            LazyRow(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .heightIn(max = 132.dp),
                                contentPadding = PaddingValues(vertical = 8.dp),
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                            ) {
                                rowItems(albumContents.first, key = { it.lowercase(Locale.ROOT) }) { folder ->
                                    FolderTile(
                                        name = folder.substringAfterLast('/'),
                                        itemCount = media.count {
                                            albumContainsMedia(parentAlbum(it.relativePath), folder)
                                        },
                                        photoCount = media.count {
                                            isPhotoInAlbum(it.relativePath, it.isVideo, folder)
                                        },
                                        onClick = { navigateAlbum(folder) },
                                        onSelectPhotos = { selectFolderPhotos(folder) },
                                    )
                                }
                            }
                        }
                        val photosInCurrentAlbum = media.count {
                            isPhotoInAlbum(it.relativePath, it.isVideo, currentAlbum)
                        }
                        if (photosInCurrentAlbum > 0) {
                            TextButton(
                                modifier = Modifier.fillMaxWidth(),
                                enabled = !isScanning,
                                onClick = { selectFolderPhotos(currentAlbum) },
                            ) {
                                Text(
                                    "Select $photosInCurrentAlbum photo(s) in this folder and subfolders",
                                    modifier = Modifier.fillMaxWidth(),
                                )
                            }
                        }

                        if (filteredMedia.isEmpty()) {
                            Box(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .weight(1f),
                                contentAlignment = Alignment.Center,
                            ) {
                                Text(
                                    when {
                                        media.isEmpty() -> "No supported media in this folder."
                                        albumContents.second.isEmpty() && albumContents.first.isEmpty() ->
                                            "This album is empty."
                                        searchText.isNotBlank() -> "No matching files."
                                        else -> "No media in this folder or its subfolders."
                                    },
                                )
                            }
                        } else {
                            LazyVerticalGrid(
                                modifier = Modifier.weight(1f),
                                columns = GridCells.Adaptive(minSize = 112.dp),
                                contentPadding = PaddingValues(vertical = 8.dp),
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                                verticalArrangement = Arrangement.spacedBy(8.dp),
                            ) {
                                items(filteredMedia, key = { it.uri.toString() }) { item ->
                                    MediaTile(
                                        item = item,
                                        selected = item.relativePath in selectedPaths,
                                        onClick = {
                                            if (selectionMode) {
                                                selectedPaths = if (item.relativePath in selectedPaths) {
                                                    selectedPaths - item.relativePath
                                                } else {
                                                    selectedPaths + item.relativePath
                                                }
                                            } else if (item.isVideo) {
                                                try {
                                                    context.startActivity(
                                                        Intent(Intent.ACTION_VIEW)
                                                            .setDataAndType(item.uri, "video/*")
                                                            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION),
                                                    )
                                                } catch (_: ActivityNotFoundException) {
                                                    message = "No app is available to play this video."
                                                } catch (error: SecurityException) {
                                                    message = "The video couldn't be opened: ${error.localizedMessage ?: "access denied"}"
                                                }
                                            } else {
                                                selectedItem = item
                                            }
                                        },
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    if (moveDialogOpen) {
        val destinations = listOf("") + folders
        AlertDialog(
            onDismissRequest = { moveDialogOpen = false },
            title = { Text("Move selected files") },
            text = {
                Column(
                    modifier = Modifier
                        .heightIn(max = 360.dp)
                        .verticalScroll(rememberScrollState()),
                ) {
                    Text("Choose a destination folder. The folder provider may not support moving files.")
                    destinations.forEach { destination ->
                        TextButton(
                            modifier = Modifier.fillMaxWidth(),
                            onClick = { moveSelectedTo(destination) },
                        ) {
                            Text(
                                if (destination.isEmpty()) "Library root (All media)" else destination,
                                modifier = Modifier.fillMaxWidth(),
                            )
                        }
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = { moveDialogOpen = false }) {
                    Text("Cancel")
                }
            },
        )
    }

    if (renameDialogOpen) {
        val selectedItems = media
            .filter { it.relativePath in selectedPaths }
            .sortedBy { it.relativePath.lowercase(Locale.ROOT) }
        val prefix = renamePrefix.trim()
        val renameError = validateRenamePrefix(prefix, selectedItems, media)
        val previews = selectedItems.mapIndexed { index, item ->
            item.name to "$prefix${index + 1}${fileExtension(item.name)}"
        }
        AlertDialog(
            onDismissRequest = { renameDialogOpen = false },
            title = { Text("Rename selected files") },
            text = {
                Column {
                    Text("Files keep their folder and extension. A number is added after the prefix.")
                    OutlinedTextField(
                        value = renamePrefix,
                        onValueChange = { renamePrefix = it },
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(top = 8.dp),
                        label = { Text("Name prefix") },
                        singleLine = true,
                        isError = renameError != null && prefix.isNotEmpty(),
                        supportingText = renameError?.let { error -> { Text(error) } },
                    )
                    if (prefix.isNotEmpty() && renameError == null) {
                        Column(
                            modifier = Modifier
                                .heightIn(max = 180.dp)
                                .verticalScroll(rememberScrollState())
                                .padding(top = 4.dp),
                        ) {
                            previews.forEach { (oldName, newName) ->
                                Text(
                                    "$oldName → $newName",
                                    style = MaterialTheme.typography.bodySmall,
                                )
                            }
                        }
                    }
                }
            },
            confirmButton = {
                TextButton(
                    enabled = previews.isNotEmpty() && renameError == null && !isScanning,
                    onClick = { renameSelected(prefix) },
                ) {
                    Text("Rename")
                }
            },
            dismissButton = {
                TextButton(onClick = { renameDialogOpen = false }) {
                    Text("Cancel")
                }
            },
        )
    }

    duplicateResult?.let { result ->
        DuplicateResultsDialog(
            result = result,
            onOpen = { item ->
                duplicateResult = null
                if (item.isVideo) {
                    try {
                        context.startActivity(
                            Intent(Intent.ACTION_VIEW)
                                .setDataAndType(item.uri, "video/*")
                                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION),
                        )
                    } catch (_: ActivityNotFoundException) {
                        message = "No app is available to play this video."
                    } catch (error: SecurityException) {
                        message = "The video couldn't be opened: ${error.localizedMessage ?: "access denied"}"
                    }
                } else {
                    selectedItem = item
                }
            },
            onDismiss = { duplicateResult = null },
        )
    }

    similarPhotoResult?.let { result ->
        SimilarPhotoResultsDialog(
            result = result,
            onOpen = { item ->
                similarPhotoResult = null
                if (item.isVideo) {
                    try {
                        context.startActivity(
                            Intent(Intent.ACTION_VIEW)
                                .setDataAndType(item.uri, "video/*")
                                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION),
                        )
                    } catch (_: ActivityNotFoundException) {
                        message = "No app is available to play this video."
                    } catch (error: SecurityException) {
                        message = "The video couldn't be opened: ${error.localizedMessage ?: "access denied"}"
                    }
                } else {
                    selectedItem = item
                }
            },
            onDismiss = { similarPhotoResult = null },
        )
    }

    if (tagManagerOpen) {
        TagManagerDialog(
            tags = libraryTags,
            selectedTag = selectedManagedTag,
            renameText = tagRenameText,
            enabled = !tagChangeRunning,
            onSelect = { name ->
                selectedManagedTag = name
                tagRenameText = name
            },
            onRename = { oldName, newName -> renameLibraryTag(oldName, newName) },
            onDelete = {
                tagDeleteConfirmation = selectedManagedTag
                tagManagerOpen = false
            },
            onRenameTextChange = { tagRenameText = it },
            onDismiss = {
                tagManagerOpen = false
                selectedManagedTag = null
            },
        )
    }
    tagDeleteConfirmation?.let { name ->
        AlertDialog(
            onDismissRequest = { tagDeleteConfirmation = null },
            title = { Text("Delete tag?") },
            text = { Text("Remove \"$name\" from every item in this library?") },
            confirmButton = {
                TextButton(
                    enabled = !tagChangeRunning,
                    onClick = {
                        tagDeleteConfirmation = null
                        deleteLibraryTag(name)
                    },
                ) {
                    Text("Delete")
                }
            },
            dismissButton = {
                TextButton(onClick = { tagDeleteConfirmation = null }) {
                    Text("Cancel")
                }
            },
        )
    }

    if (createAlbumDialogOpen) {
        val trimmedName = newAlbumName.trim()
        val albumNameError = validateAlbumName(trimmedName, folders, currentAlbum)
        AlertDialog(
            onDismissRequest = { createAlbumDialogOpen = false },
            title = { Text("Create album") },
            text = {
                Column {
                    Text(
                        if (currentAlbum.isEmpty()) "Create a folder in the library root."
                        else "Create a folder inside $currentAlbum.",
                    )
                    OutlinedTextField(
                        value = newAlbumName,
                        onValueChange = { newAlbumName = it },
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(top = 8.dp),
                        label = { Text("Album name") },
                        singleLine = true,
                        isError = albumNameError != null && trimmedName.isNotEmpty(),
                        supportingText = albumNameError?.let { error -> { Text(error) } },
                    )
                }
            },
            confirmButton = {
                TextButton(
                    enabled = trimmedName.isNotEmpty() && albumNameError == null && !isScanning,
                    onClick = { createAlbum(trimmedName) },
                ) {
                    Text("Create")
                }
            },
            dismissButton = {
                TextButton(onClick = { createAlbumDialogOpen = false }) {
                    Text("Cancel")
                }
            },
        )
    }

    selectedItem?.let { item ->
        val selectedIndex = previewPhotos.indexOfFirst { it.uri == item.uri }
        val shownIndex = if (selectedIndex >= 0) selectedIndex else 0
        val displayedItem = previewPhotos.getOrNull(shownIndex) ?: item
        Dialog(onDismissRequest = { selectedItem = null }) {
            Surface(shape = RoundedCornerShape(16.dp)) {
                Column(modifier = Modifier.padding(12.dp)) {
                    AsyncImage(
                        model = displayedItem.uri,
                        contentDescription = displayedItem.name,
                        modifier = Modifier
                            .fillMaxWidth()
                            .heightIn(max = 600.dp)
                            .clip(RoundedCornerShape(8.dp)),
                        contentScale = ContentScale.Fit,
                    )
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(top = 8.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        TextButton(
                            enabled = shownIndex > 0,
                            onClick = { selectedItem = previewPhotos[shownIndex - 1] },
                        ) {
                            Text("Previous")
                        }
                        Text(
                            if (previewPhotos.isEmpty()) "Photo" else "${shownIndex + 1} of ${previewPhotos.size}",
                            style = MaterialTheme.typography.labelMedium,
                        )
                        TextButton(
                            enabled = shownIndex < previewPhotos.lastIndex,
                            onClick = { selectedItem = previewPhotos[shownIndex + 1] },
                        ) {
                            Text("Next")
                        }
                    }
                    Text(
                        displayedItem.relativePath,
                        modifier = Modifier.padding(top = 10.dp, start = 4.dp),
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
            }
        }
    }

    tagDialogPaths?.let { paths ->
        val selectedItems = media.filter { it.relativePath in paths }
        TagDialog(
            items = selectedItems,
            onAdd = { names ->
                changeTags(paths, names)
                tagDialogPaths = null
            },
            onRemove = { name ->
                changeTags(paths, listOf(name), remove = true)
                tagDialogPaths = null
            },
            onDismiss = { tagDialogPaths = null },
        )
    }
}

@Composable
private fun FolderTile(
    name: String,
    itemCount: Int,
    photoCount: Int,
    onClick: () -> Unit,
    onSelectPhotos: () -> Unit,
) {
    Surface(
        modifier = Modifier
            .heightIn(min = 60.dp),
        shape = RoundedCornerShape(10.dp),
        color = MaterialTheme.colorScheme.secondaryContainer,
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
            verticalArrangement = Arrangement.Center,
        ) {
            Column(
                modifier = Modifier
                    .clickable(onClick = onClick)
                    .padding(horizontal = 4.dp, vertical = 4.dp),
            ) {
                Text(
                    "📁 $name",
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    style = MaterialTheme.typography.titleSmall,
                )
                Text("$itemCount item(s)", style = MaterialTheme.typography.labelSmall)
            }
            TextButton(
                enabled = photoCount > 0,
                onClick = onSelectPhotos,
            ) {
                Text("Select $photoCount photos", style = MaterialTheme.typography.labelSmall)
            }
        }
    }
}

@Composable
private fun DuplicateResultsDialog(
    result: DuplicateScanResult,
    onOpen: (MediaItem) -> Unit,
    onDismiss: () -> Unit,
) {
    Dialog(onDismissRequest = onDismiss) {
        Surface(shape = RoundedCornerShape(16.dp)) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(max = 600.dp)
                    .verticalScroll(rememberScrollState())
                    .padding(16.dp),
            ) {
                Text("Exact duplicates", style = MaterialTheme.typography.titleLarge)
                Text(
                    "${result.groups.size} group(s), " +
                        "${result.groups.sumOf { it.items.size }} duplicate file(s); " +
                        "${result.filesHashed} candidate(s) checked.",
                    modifier = Modifier.padding(top = 4.dp),
                    style = MaterialTheme.typography.bodySmall,
                )
                Text(
                    "Files are only compared here. Nothing is deleted.",
                    modifier = Modifier.padding(top = 4.dp),
                    style = MaterialTheme.typography.bodySmall,
                )
                if (result.unknownSizeFiles > 0) {
                    Text(
                        "${result.unknownSizeFiles} file(s) with unknown size weren't checked.",
                        modifier = Modifier.padding(top = 8.dp),
                        color = MaterialTheme.colorScheme.secondary,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
                if (result.unreadableFiles.isNotEmpty()) {
                    Text(
                        "${result.unreadableFiles.size} file(s) couldn't be read:",
                        modifier = Modifier.padding(top = 8.dp),
                        color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall,
                    )
                    result.unreadableFiles.take(3).forEach { failure ->
                        Text(
                            failure,
                            color = MaterialTheme.colorScheme.error,
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                }
                if (result.groups.isEmpty()) {
                    Text(
                        "No exact duplicates found.",
                        modifier = Modifier.padding(top = 16.dp),
                        style = MaterialTheme.typography.bodyMedium,
                    )
                } else {
                    result.groups.forEachIndexed { index, group ->
                        Column(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(top = 12.dp),
                        ) {
                            Text(
                                "Group ${index + 1} · ${formatByteSize(group.size)} · ${group.items.size} files",
                                style = MaterialTheme.typography.titleSmall,
                            )
                            group.items.forEach { item ->
                                TextButton(
                                    modifier = Modifier.fillMaxWidth(),
                                    onClick = { onOpen(item) },
                                ) {
                                    Text(
                                        item.relativePath,
                                        modifier = Modifier.fillMaxWidth(),
                                        maxLines = 2,
                                        overflow = TextOverflow.Ellipsis,
                                    )
                                }
                            }
                        }
                    }
                }
                TextButton(
                    modifier = Modifier.align(Alignment.End),
                    onClick = onDismiss,
                ) {
                    Text("Done")
                }
            }
        }
    }
}

@Composable
private fun SimilarPhotoResultsDialog(
    result: SimilarPhotoScanResult,
    onOpen: (MediaItem) -> Unit,
    onDismiss: () -> Unit,
) {
    Dialog(onDismissRequest = onDismiss) {
        Surface(shape = RoundedCornerShape(16.dp)) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(max = 600.dp)
                    .verticalScroll(rememberScrollState())
                    .padding(16.dp),
            ) {
                Text("Similar media", style = MaterialTheme.typography.titleLarge)
                Text(
                    "${result.groups.size} group(s), " +
                        "${result.groups.sumOf { it.items.size }} item(s) in groups; " +
                        "${result.photosChecked} photo(s) and ${result.videosChecked} video(s) checked.",
                    modifier = Modifier.padding(top = 4.dp),
                    style = MaterialTheme.typography.bodySmall,
                )
                Text(
                    "Matches are based on visual resemblance and can include false positives. " +
                        "Nothing is deleted.",
                    modifier = Modifier.padding(top = 4.dp),
                    style = MaterialTheme.typography.bodySmall,
                )
                if (result.unreadableFiles.isNotEmpty()) {
                    Text(
                        "${result.unreadableFiles.size} photo(s) couldn't be read:",
                        modifier = Modifier.padding(top = 8.dp),
                        color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall,
                    )
                    result.unreadableFiles.take(3).forEach { failure ->
                        Text(
                            failure,
                            color = MaterialTheme.colorScheme.error,
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                }
                if (result.groups.isEmpty()) {
                    Text(
                        "No similar photos or videos found.",
                        modifier = Modifier.padding(top = 16.dp),
                        style = MaterialTheme.typography.bodyMedium,
                    )
                } else {
                    result.groups.forEachIndexed { index, group ->
                        Column(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(top = 12.dp),
                        ) {
                            Text(
                                "Group ${index + 1} · ${group.items.size} " +
                                    if (group.isVideo) "videos" else "photos",
                                style = MaterialTheme.typography.titleSmall,
                            )
                            group.items.forEach { item ->
                                TextButton(
                                    modifier = Modifier.fillMaxWidth(),
                                    onClick = { onOpen(item) },
                                ) {
                                    Text(
                                        item.relativePath,
                                        modifier = Modifier.fillMaxWidth(),
                                        maxLines = 2,
                                        overflow = TextOverflow.Ellipsis,
                                    )
                                }
                            }
                        }
                    }
                }
                TextButton(
                    modifier = Modifier.align(Alignment.End),
                    onClick = onDismiss,
                ) {
                    Text("Done")
                }
            }
        }
    }
}

@Composable
private fun MediaTile(item: MediaItem, selected: Boolean, onClick: () -> Unit) {
    Column(
        modifier = Modifier
            .clip(RoundedCornerShape(10.dp))
            .clickable(onClick = onClick),
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .aspectRatio(1f)
                .background(MaterialTheme.colorScheme.surfaceVariant),
            contentAlignment = Alignment.Center,
        ) {
            if (item.isVideo) {
                Text("▶", style = MaterialTheme.typography.headlineMedium)
                Text(
                    "VIDEO",
                    modifier = Modifier
                        .align(Alignment.BottomStart)
                        .padding(8.dp),
                    style = MaterialTheme.typography.labelSmall,
                )
            } else {
                AsyncImage(
                    model = item.uri,
                    contentDescription = item.name,
                    modifier = Modifier.fillMaxSize(),
                    contentScale = ContentScale.Crop,
                )
            }
            if (selected) {
                Surface(
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(6.dp),
                    shape = RoundedCornerShape(50),
                    color = MaterialTheme.colorScheme.primary,
                ) {
                    Text(
                        "Selected",
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                        color = MaterialTheme.colorScheme.onPrimary,
                        style = MaterialTheme.typography.labelSmall,
                    )
                }
            }
        }
        Text(
            item.name,
            modifier = Modifier.padding(horizontal = 4.dp, vertical = 6.dp),
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            style = MaterialTheme.typography.bodySmall,
        )
        if (item.tags.isNotEmpty()) {
            Text(
                item.tags.joinToString(", "),
                modifier = Modifier.padding(horizontal = 4.dp),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.secondary,
            )
        }
    }
}

@Composable
private fun TagDialog(
    items: List<MediaItem>,
    onAdd: (List<String>) -> Unit,
    onRemove: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    var input by remember { mutableStateOf("") }
    val tagCounts = remember(items) {
        val counts = linkedMapOf<String, Pair<String, Int>>()
        items.flatMap { it.tags }.forEach { name ->
            val key = name.lowercase(Locale.ROOT)
            val previous = counts[key]
            counts[key] = (previous?.first ?: name) to ((previous?.second ?: 0) + 1)
        }
        counts.values.sortedBy { it.first.lowercase(Locale.ROOT) }
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Tags for ${items.size} item(s)") },
        text = {
            Column {
                OutlinedTextField(
                    value = input,
                    onValueChange = { input = it },
                    modifier = Modifier.fillMaxWidth(),
                    label = { Text("Add tags, separated by commas") },
                    singleLine = true,
                )
                TextButton(
                    enabled = parseTagNames(input).isNotEmpty(),
                    onClick = {
                        onAdd(parseTagNames(input))
                        input = ""
                    },
                ) {
                    Text("Add to selection")
                }
                if (tagCounts.isNotEmpty()) {
                    Text("Remove a tag from all selected items:")
                    Column(
                        modifier = Modifier
                            .heightIn(max = 240.dp)
                            .verticalScroll(rememberScrollState()),
                    ) {
                        tagCounts.forEach { (name, count) ->
                            TextButton(onClick = { onRemove(name) }) {
                                Text("$name ($count/${items.size})")
                            }
                        }
                    }
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) {
                Text("Done")
            }
        },
    )
}

@Composable
private fun TagManagerDialog(
    tags: List<TagCount>,
    selectedTag: String?,
    renameText: String,
    enabled: Boolean,
    onSelect: (String) -> Unit,
    onRename: (String, String) -> Unit,
    onDelete: () -> Unit,
    onRenameTextChange: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    val normalizedName = parseTagNames(renameText).singleOrNull()
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Manage tags") },
        text = {
            Column {
                Text("Choose a tag to rename or remove from this library.")
                Column(
                    modifier = Modifier
                        .heightIn(max = 220.dp)
                        .verticalScroll(rememberScrollState()),
                ) {
                    tags.forEach { tag ->
                        TextButton(
                            modifier = Modifier.fillMaxWidth(),
                            enabled = enabled,
                            onClick = { onSelect(tag.name) },
                        ) {
                            Text(
                                "${tag.name} (${tag.count})" +
                                    if (tag.name == selectedTag) "  ✓" else "",
                                modifier = Modifier.fillMaxWidth(),
                            )
                        }
                    }
                }
                if (selectedTag != null) {
                    OutlinedTextField(
                        value = renameText,
                        onValueChange = onRenameTextChange,
                        modifier = Modifier.fillMaxWidth(),
                        label = { Text("New tag name") },
                        singleLine = true,
                    )
                    TextButton(
                        enabled = enabled,
                        onClick = onDelete,
                    ) {
                        Text("Delete \"$selectedTag\" from all items")
                    }
                }
            }
        },
        confirmButton = {
            TextButton(
                enabled = enabled && selectedTag != null && !normalizedName.isNullOrBlank(),
                onClick = {
                    selectedTag?.let { oldName ->
                        normalizedName?.let { newName -> onRename(oldName, newName) }
                    }
                },
            ) {
                Text("Rename / merge")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text("Done")
            }
        },
    )
}

private fun parentAlbum(relativePath: String): String =
    relativePath.substringBeforeLast('/', missingDelimiterValue = "")

private fun validateAlbumName(name: String, folders: List<String>, parent: String): String? {
    if (name.isEmpty()) return "Enter an album name."
    if (name == "." || name == ".." || '/' in name || '\\' in name || name.startsWith(".")) {
        return "Use a folder name without path separators or a leading dot."
    }
    val duplicate = folders.any { folder ->
        parentAlbum(folder).equals(parent, ignoreCase = true) &&
            folder.substringAfterLast('/').equals(name, ignoreCase = true)
    }
    return if (duplicate) "An album with that name already exists here." else null
}

private fun fileExtension(name: String): String {
    val dot = name.lastIndexOf('.')
    return if (dot > 0) name.substring(dot) else ""
}

private fun validateRenamePrefix(
    prefix: String,
    selectedItems: List<MediaItem>,
    allMedia: List<MediaItem>,
): String? {
    if (prefix.isEmpty()) return "Enter a name prefix."
    if ('/' in prefix || '\\' in prefix || prefix == "." || prefix == "..") {
        return "A prefix cannot contain a path separator."
    }
    if (selectedItems.isEmpty()) return "Select at least one file."

    val generatedPaths = mutableSetOf<String>()
    selectedItems.sortedBy { it.relativePath.lowercase(Locale.ROOT) }
        .forEachIndexed { index, item ->
            val extension = fileExtension(item.name)
            val name = "$prefix${index + 1}$extension"
            val parent = parentAlbum(item.relativePath)
            val destination = if (parent.isEmpty()) name else "$parent/$name"
            val key = destination.lowercase(Locale.ROOT)
            if (!generatedPaths.add(key)) return "The new names would collide."
            if (allMedia.any {
                    it.relativePath.lowercase(Locale.ROOT) == key &&
                        !it.relativePath.equals(item.relativePath, ignoreCase = true)
                }
            ) {
                return "A file with one of the new names already exists."
            }
        }
    return null
}

private fun queryDisplayName(context: Context, uri: Uri): String? {
    context.contentResolver.query(
        uri,
        arrayOf(OpenableColumns.DISPLAY_NAME),
        null,
        null,
        null,
    )?.use { cursor ->
        val nameColumn = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
        if (nameColumn >= 0 && cursor.moveToFirst()) return cursor.getString(nameColumn)
    }
    return null
}

private fun formatByteSize(size: Long): String {
    val units = listOf("B", "KB", "MB", "GB", "TB")
    var value = size.toDouble()
    var unit = 0
    while (value >= 1024 && unit < units.lastIndex) {
        value /= 1024
        unit++
    }
    return if (unit == 0) "$size ${units[unit]}" else "%.1f %s".format(Locale.ROOT, value, units[unit])
}
