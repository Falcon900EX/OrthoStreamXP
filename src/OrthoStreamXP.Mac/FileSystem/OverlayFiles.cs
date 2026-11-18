using System.Buffers.Binary;
using System.Collections.Concurrent;
using Microsoft.Win32.SafeHandles;
using OrthoStreamXP.Scenery;
using OrthoStreamXP.Textures;

namespace OrthoStreamXP.Mac;

internal sealed class FileSystemError(int error, string message) : IOException(message)
{
    public int Error { get; } = error;
}

internal sealed record FileStat(bool Directory, long Size, double Modified, ulong Inode);
internal sealed record DirectoryPage(string[] Names, bool End);

/// <summary>
/// POSIX view of the existing engine. Handles retain lengths and open files over atomic
/// cache replacement. Read cursors, night textures and colour transforms match WinFsp.
/// </summary>
internal sealed class OverlayFiles(MountContext context) : IDisposable
{
    public const int MaxRead = 4 * 1024 * 1024;
    private const int MaxHandles = 65536;
    private static readonly long MountedAt = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
    private readonly ConcurrentDictionary<long, Handle> _handles = new();
    private long _nextHandle;

    private sealed class Handle(VirtualPath path, FileStat stat) : IDisposable
    {
        public object Sync { get; } = new();
        public VirtualPath Path { get; } = path;
        public FileStat Stat { get; } = stat;
        public long Length => Stat.Size;
        public bool Closed { get; private set; }
        public SafeFileHandle? File { get; init; }
        public StreamedTexture? Streamed { get; init; }
        public byte[]? Content { get; init; }
        public NightTexture? Night { get; init; }
        public StreamedTexture? Photo { get; init; }
        public ReadCursor? Cursor { get; init; }
        public TextureName SeasonName { get; init; }
        public int SeasonBlockBytes { get; init; }
        public int SeasonSize { get; init; }
        public DateTime FileVersion { get; init; }
        public string[]? Entries { get; init; }

        public void Dispose()
        {
            lock (Sync)
            {
                if (Closed) return;
                Closed = true;
                File?.Dispose();
            }
        }
    }

    public FileStat Stat(string name)
    {
        VirtualPath path = Parse(name);
        return Describe(path, context.Overlay.Open(path));
    }

    public FileStat Stat(long id)
    {
        Handle handle = Get(id);
        lock (handle.Sync)
        {
            CheckOpen(handle);
            return handle.Streamed is { } streamed
                ? handle.Stat with { Modified = UnixSeconds(TextureStreamer.VersionOf(streamed)) }
                : handle.Stat;
        }
    }

    public long Open(string name, bool directory = false)
    {
        if (_handles.Count >= MaxHandles) throw new FileSystemError(24, "Too many open scenery handles");
        VirtualPath path = Parse(name);
        OverlayEntry entry = context.Overlay.Open(path);
        FileStat stat = Describe(path, entry);
        if (directory != stat.Directory)
            throw new FileSystemError(directory ? 20 : 21, directory ? "Not a directory" : "Is a directory");
        Handle handle;
        if (directory)
        {
            handle = new Handle(path, stat) { Entries = [.. context.Overlay.List(path).Select(e => e.Name)] };
        }
        else if (entry.Streamed is { } streamed)
        {
            context.Usage?.Note(Setup.AreaName.Of(streamed.Name));
            handle = new Handle(path, stat)
            {
                Streamed = streamed, Cursor = new ReadCursor(), SeasonName = streamed.Name,
                SeasonBlockBytes = Recolours ? 8 : 0, SeasonSize = streamed.Layout.Size,
            };
            context.ReadLog?.Open(context.Plan.Name, path, "streamed");
        }
        else if (entry.Content is { } content)
        {
            handle = new Handle(path, stat) { Content = content };
            context.ReadLog?.Open(context.Plan.Name, path, "memory");
        }
        else if (entry.Night is { } night)
        {
            handle = new Handle(path, stat) { Night = night, Cursor = new ReadCursor() };
            context.ReadLog?.Open(context.Plan.Name, path, "night");
        }
        else
        {
            string backing = entry.Backing!;
            SafeFileHandle file = File.OpenHandle(backing, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            try
            {
                long length = RandomAccess.GetLength(file);
                StreamedTexture? photo = context.Overlay.Streamer?.Get(path) is { } t && t.Layout.Length == length ? t : null;
                var (seasonName, blockBytes, size) = SeasonTexture(path, file);
                handle = new Handle(path, stat with { Size = length })
                {
                    File = file, Photo = photo, Cursor = photo is null ? null : new ReadCursor(),
                    SeasonName = seasonName, SeasonBlockBytes = blockBytes, SeasonSize = size,
                    FileVersion = blockBytes > 0 ? File.GetLastWriteTimeUtc(backing) : default,
                };
                if (path.IsDds && TextureName.TryParseFile(path.Name, out TextureName used))
                    context.Usage?.Note(Setup.AreaName.Of(used));
                context.ReadLog?.Open(context.Plan.Name, path, "file");
                context.Loads.Opened(path, context.Plan.Name);
            }
            catch
            {
                file.Dispose();
                throw;
            }
        }
        long id = Interlocked.Increment(ref _nextHandle);
        if (!_handles.TryAdd(id, handle))
        {
            handle.Dispose();
            throw new IOException("Handle allocation failed");
        }
        return id;
    }

    public DirectoryPage List(long id, long offset)
    {
        if (offset < 0 || offset > int.MaxValue) throw new FileSystemError(22, "Invalid directory offset");
        Handle handle = Get(id);
        lock (handle.Sync)
        {
            CheckOpen(handle);
            if (handle.Entries is not { } entries) throw new FileSystemError(20, "Not a directory");
            int start = (int)Math.Min(offset, entries.Length);
            int count = Math.Min(512, entries.Length - start);
            return new DirectoryPage(entries[start..(start + count)], start + count == entries.Length);
        }
    }

    public byte[] Read(long id, long offset, int size)
    {
        if (offset < 0 || size < 0 || size > MaxRead) throw new FileSystemError(22, "Invalid read range");
        Handle handle = Get(id);
        lock (handle.Sync)
        {
            CheckOpen(handle);
            if (handle.Stat.Directory) throw new FileSystemError(21, "Is a directory");
            if (offset >= handle.Length || size == 0) return [];
            int count = (int)Math.Min(size, handle.Length - offset);
            byte[] data = new byte[count];
            TextureColour? colour = ColourOf(handle);
            if (handle.Streamed is { } streamed)
            {
                ReadAnswer answer = context.Overlay.Streamer!.Read(streamed, handle.Cursor!, offset, data);
                count = answer.Bytes;
                Recolor(handle, data.AsSpan(0, count), (ulong)offset, colour);
                LogTextureRead(handle.Path, offset, answer, streamed);
            }
            else if (handle.Content is { } content)
            {
                content.AsSpan((int)offset, count).CopyTo(data);
                context.ReadLog?.Read(new ReadRecord(context.Plan.Name, handle.Path, offset, count));
            }
            else if (handle.Night is { } night)
            {
                count = night.Read(handle.Cursor!, offset, data);
                context.ReadLog?.Read(new ReadRecord(context.Plan.Name, handle.Path, offset, count));
            }
            else if (handle.File is { } file)
            {
                // A regular-file read is allowed to be short. Fill the range unless EOF is reached.
                int done = 0;
                while (done < count)
                {
                    int got = RandomAccess.Read(file, data.AsSpan(done), offset + done);
                    if (got == 0) break;
                    done += got;
                }
                count = done;
                Recolor(handle, data.AsSpan(0, count), (ulong)offset, colour);
                if (handle.Photo is { } photo)
                    LogTextureRead(handle.Path, offset, context.Overlay.Streamer!.NoteFileRead(photo, handle.Cursor!, offset, count), photo);
                else
                    context.ReadLog?.Read(new ReadRecord(context.Plan.Name, handle.Path, offset, count));
            }
            if (count != data.Length) Array.Resize(ref data, count);
            return data;
        }
    }

    public void Release(long id)
    {
        if (!_handles.TryRemove(id, out Handle? handle)) throw new FileSystemError(9, "Invalid scenery handle");
        handle.Dispose();
    }

    private Handle Get(long id) => _handles.TryGetValue(id, out Handle? handle)
        ? handle : throw new FileSystemError(9, "Invalid scenery handle");

    private static void CheckOpen(Handle handle)
    {
        if (handle.Closed) throw new FileSystemError(9, "Closed scenery handle");
    }

    private static VirtualPath Parse(string name)
    {
        if (name.Length > 4096 || !name.StartsWith('/')) throw new FileSystemError(22, "Invalid virtual path");
        return VirtualPath.Parse(name);
    }

    private static FileStat Describe(VirtualPath path, OverlayEntry entry)
    {
        if (entry.Streamed is { } streamed)
            return new FileStat(false, streamed.Layout.Length, UnixSeconds(TextureStreamer.VersionOf(streamed)), Inode(path));
        long? size = entry.Content?.LongLength ?? entry.Night?.Layout.Length;
        if (size is { } virtualSize) return new FileStat(false, virtualSize, MountedAt, Inode(path));
        string backing = entry.Backing ?? throw new FileNotFoundException(path.Display);
        bool directory = Directory.Exists(backing);
        FileSystemInfo info = directory ? new DirectoryInfo(backing) : new FileInfo(backing);
        if (!info.Exists) throw new FileNotFoundException(path.Display);
        return new FileStat(directory, directory ? 0 : ((FileInfo)info).Length,
            UnixSeconds(info.LastWriteTimeUtc), Inode(path));
    }

    private static double UnixSeconds(DateTime utc) => (utc.Ticks - DateTime.UnixEpoch.Ticks) / (double)TimeSpan.TicksPerSecond;

    private static ulong Inode(VirtualPath path)
    {
        // Stable for this virtual name; do not reuse backing-file inode numbers across roots.
        ulong hash = 14695981039346656037UL;
        foreach (char c in path.Key) hash = unchecked((hash ^ c) * 1099511628211UL);
        return (hash & 0x7fffffffffffffffUL) + 1;
    }

    /// <summary>Whether photo textures get any colour adjustment.</summary>
    private bool Recolours => context.Seasons is not null || context.Colours is not null || context.Exposure is not null;

    /// <summary>The evened-out colours, the season's tint and the exposure for a photo texture's bytes (the cache keeps the imagery as downloaded).</summary>
    private void Recolor(Handle handle, Span<byte> data, ulong offset, TextureColour? colour)
    {
        if (handle.SeasonBlockBytes == 0) return;
        PhotoColours.Apply(data, (long)offset, handle.SeasonBlockBytes, handle.SeasonSize, colour, context.Seasons?.TableFor(handle.SeasonName), context.Exposure);
    }

    /// <summary>
    /// A photo texture's colour correction: measured on the photo (a streamed texture's real data, or the file), or the
    /// base's while the picture is still the low-zoom base; null when there is none.
    /// </summary>
    private TextureColour? ColourOf(Handle handle)
    {
        if (context.Colours is null || handle.SeasonBlockBytes == 0) return null;
        if (handle.Streamed is { } streamed)
        {
            TextureStreamer streamer = context.Overlay.Streamer!;
            var (source, version) = streamer.Picture(streamed);
            return source == PictureSource.Photo
                ? context.Colours.ForPhoto(streamed.Name, version, size => streamer.PhotoSample(streamed, size))
                : context.Colours.ForBase(streamed.Name);
        }
        return handle.SeasonBlockBytes == 8 && handle.File is { } file
            ? context.Colours.ForPhoto(handle.SeasonName, handle.FileVersion, size => FileSample(file, handle.SeasonSize, size))
            : null;
    }

    /// <summary>The level of a DXT1 file nearest <paramref name="size"/> pixels, decoded to RGB; null when the file is not a full chain of levels.</summary>
    internal static (byte[] Rgb, int Size)? FileSample(SafeFileHandle file, int fileSize, int size)
    {
        var layout = DdsLayout.For(fileSize);
        if (RandomAccess.GetLength(file) < layout.Length) return null;
        int mip = Math.Clamp((int)Math.Round(Math.Log2((double)fileSize / size)), 0, layout.Levels - 1), levelSize = layout.SizeOf(mip);
        byte[] blocks = new byte[layout.LengthOf(mip)];
        return RandomAccess.Read(file, blocks, layout.Offset(mip)) < blocks.Length ? null : (Dds.DecodeLevel(blocks, levelSize, levelSize), levelSize);
    }

    /// <summary>A photo texture file whose colours can change: square DXT1, DXT3 or DXT5, named like 21904_35824_BI16.dds.</summary>
    private (TextureName Name, int BlockBytes, int Size) SeasonTexture(VirtualPath path, SafeFileHandle? file)
    {
        if (!Recolours || file is null || !path.IsDds || !TextureName.TryParseFile(path.Name, out TextureName name)) return default;
        Span<byte> header = stackalloc byte[Dds.HeaderSize];
        if (RandomAccess.Read(file, header, 0) < Dds.HeaderSize || !header[..4].SequenceEqual("DDS "u8)) return default;
        int height = BinaryPrimitives.ReadInt32LittleEndian(header[12..]), width = BinaryPrimitives.ReadInt32LittleEndian(header[16..]);
        int blockBytes = ColourTable.BlockBytes(header.Slice(84, 4));
        return width == height && width > 0 && blockBytes > 0 ? (name, blockBytes, width) : default;
    }

    private void LogTextureRead(VirtualPath path, long offset, ReadAnswer answer, StreamedTexture texture) =>
        context.ReadLog?.Read(new ReadRecord(context.Plan.Name, path, offset, answer.Bytes, answer.FirstMip, answer.LastMip,
            answer.Band?.ToString().ToLowerInvariant(), answer.Served?.ToString().ToLowerInvariant(), answer.WaitMs, answer.DistanceM,
            texture.SizeM, texture.Layout.Size, answer.Wanted));


    public void Dispose()
    {
        foreach (long id in _handles.Keys)
            if (_handles.TryRemove(id, out Handle? handle)) handle.Dispose();
    }
}
