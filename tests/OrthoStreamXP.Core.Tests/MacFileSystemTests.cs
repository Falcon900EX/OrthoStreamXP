using System.Buffers.Binary;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using OrthoStreamXP.Mac;
using OrthoStreamXP.Scenery;
using OrthoStreamXP.Textures;

namespace OrthoStreamXP.Tests;

public sealed class MacFileSystemTests : IDisposable
{
    private readonly TempTree _tree = new();

    public MacFileSystemTests() => Directory.CreateDirectory(_tree.PathOf("source/Earth nav data"));
    public void Dispose() => _tree.Dispose();

    private MountContext Context()
    {
        var plan = new PackagePlan("test", _tree.PathOf("source"), _tree.PathOf("cache"), _tree.PathOf("mount"));
        var overlay = new SceneryOverlay(new SceneryRoots(plan.Source, plan.Cache, null, new TexturePlan(-3)), null, lazyTerrainListing: false);
        return new MountContext(plan, overlay, null, null, null, null, new SceneryLoads(new ViewTracker()), null);
    }

    [Fact]
    public void Generated_ini_loads_using_the_real_settings_parser()
    {
        Directory.CreateDirectory(_tree.PathOf("X-Plane/Custom Scenery"));
        string file = _tree.PathOf("mac.ini");
        MacConfiguration.Initialize(file, _tree.PathOf("source"), false, _tree.PathOf("X-Plane"), _tree.PathOf("cache"));
        MountSettings settings = MountSettings.FromValues(SettingsOptions.Schema.ReadFile(file),
            validateMountpoints: false, migrateLegacy: false, allowSourceInCustomScenery: true);
        Assert.Equal(new[] { "bing", "esri" }, settings.Providers!.Select(p => p.Name));
        Assert.True(settings.TrackAircraft);
        Assert.True(settings.LazyTerrainListing);
        Assert.False(settings.UpdateSceneryPacks);
        Assert.Throws<IOException>(() => MacConfiguration.Initialize(file, "unused", false, "unused", null));
    }

    [Fact]
    public void Native_paths_use_the_host_separator()
    {
        var roots = new SceneryRoots(_tree.PathOf("source"), _tree.PathOf("cache"), null, new TexturePlan(-1));
        Assert.Equal(_tree.PathOf("cache/textures/water.png"), roots.CachePath(VirtualPath.Parse("/textures/water.png")));
    }

    [Theory]
    [InlineData("/../secret")]
    [InlineData("/textures/../secret")]
    [InlineData("/textures/a:stream")]
    [InlineData("/textures/a\0b")]
    public void Invalid_paths_are_rejected(string path) => Assert.Throws<NtStatusException>(() => VirtualPath.Parse(path));

    [Fact]
    public void Backing_file_supports_offset_reads_short_reads_and_eof()
    {
        _tree.WriteText("source/test.bin", "0123456789");
        using var files = new OverlayFiles(Context());
        long handle = files.Open("/test.bin");
        Assert.Equal("2345"u8.ToArray(), files.Read(handle, 2, 4));
        Assert.Equal("89"u8.ToArray(), files.Read(handle, 8, 20));
        Assert.Empty(files.Read(handle, 10, 5));
        Assert.Empty(files.Read(handle, 0, 0));
        Assert.Equal(10, files.Stat(handle).Size);
        files.Release(handle);
        Assert.Equal(9, Assert.Throws<FileSystemError>(() => files.Read(handle, 0, 1)).Error);
    }

    [Fact]
    public void Open_handle_keeps_old_inode_and_length_over_atomic_replacement()
    {
        string path = _tree.WriteText("source/file.bin", "old-bytes");
        using var files = new OverlayFiles(Context());
        long first = files.Open("/file.bin");
        string replacement = _tree.WriteText("replacement", "new");
        File.Move(replacement, path, overwrite: true);
        long second = files.Open("/file.bin");
        Assert.Equal("old-bytes"u8.ToArray(), files.Read(first, 0, 99));
        Assert.Equal("new"u8.ToArray(), files.Read(second, 0, 99));
        Assert.Equal(9, files.Stat(first).Size);
        Assert.Equal(3, files.Stat(second).Size);
    }

    [Fact]
    public void Terrain_definitions_are_generated_in_memory()
    {
        using var files = new OverlayFiles(Context());
        long handle = files.Open("/terrain/21904_35824_BI16.ter");
        string text = Encoding.UTF8.GetString(files.Read(handle, 0, 65536));
        Assert.Contains("BASE_TEX_NOWRAP ../textures/21904_35824_BI16.dds", text, StringComparison.Ordinal);
        Assert.Equal(Encoding.UTF8.GetByteCount(text), files.Stat(handle).Size);
    }

    [Fact]
    public void Root_is_a_directory_and_directory_handles_paginate()
    {
        for (int i = 0; i < 520; i++) _tree.WriteText($"source/textures/item{i:000}.png", "x");
        using var files = new OverlayFiles(Context());
        Assert.True(files.Stat("/").Directory);
        long handle = files.Open("/textures", directory: true);
        DirectoryPage first = files.List(handle, 0);
        DirectoryPage second = files.List(handle, first.Names.Length);
        Assert.Equal(512, first.Names.Length);
        Assert.False(first.End);
        Assert.Equal(8, second.Names.Length);
        Assert.True(second.End);
        Assert.Equal(520, first.Names.Concat(second.Names).Distinct().Count());
        Assert.Equal(21, Assert.Throws<FileSystemError>(() => files.Read(handle, 0, 1)).Error);
    }

    [Fact]
    public void Oversized_and_negative_reads_are_rejected()
    {
        _tree.WriteText("source/file.bin", "x");
        using var files = new OverlayFiles(Context());
        long handle = files.Open("/file.bin");
        Assert.Equal(22, Assert.Throws<FileSystemError>(() => files.Read(handle, -1, 1)).Error);
        Assert.Equal(22, Assert.Throws<FileSystemError>(() => files.Read(handle, 0, OverlayFiles.MaxRead + 1)).Error);
    }

    [Fact]
    public void Missing_file_maps_to_posix_not_found()
    {
        using var files = new OverlayFiles(Context());
        Exception error = Assert.Throws<NtStatusException>(() => files.Open("/missing"));
        Assert.Equal(2, EngineServer.ErrorNumber(error));
    }

    [Fact]
    public async Task Socket_protocol_serves_binary_data_and_posix_errors()
    {
        _tree.WriteText("source/a.bin", "abcdefgh");
        using var server = new EngineServer(Context());
        using var socket = new Socket(AddressFamily.Unix, SocketType.Stream, ProtocolType.Unspecified);
        await socket.ConnectAsync(new UnixDomainSocketEndPoint(server.SocketPath), TestContext.Current.CancellationToken);
        using var stream = new NetworkStream(socket, ownsSocket: false);
        var opened = await ExchangeAsync(stream, new { v = 1, op = "open", path = "/a.bin" });
        Assert.Equal(0, opened.Error);
        using JsonDocument document = JsonDocument.Parse(opened.Data);
        long fh = document.RootElement.GetProperty("fh").GetInt64();
        var read = await ExchangeAsync(stream, new { v = 1, op = "read", fh, offset = 3, size = 3 });
        Assert.Equal(0, read.Error);
        Assert.Equal("def"u8.ToArray(), read.Data);
        var missing = await ExchangeAsync(stream, new { v = 1, op = "stat", path = "/missing" });
        Assert.Equal(2, missing.Error);
        var version = await ExchangeAsync(stream, new { v = 99, op = "ping" });
        Assert.Equal(22, version.Error);
    }

    private static async Task<(int Error, byte[] Data)> ExchangeAsync(NetworkStream stream, object request)
    {
        CancellationToken cancellation = TestContext.Current.CancellationToken;
        byte[] payload = JsonSerializer.SerializeToUtf8Bytes(request);
        byte[] frame = new byte[4 + payload.Length];
        BinaryPrimitives.WriteInt32LittleEndian(frame, payload.Length);
        payload.CopyTo(frame, 4);
        await stream.WriteAsync(frame, cancellation);
        byte[] header = new byte[8];
        await stream.ReadExactlyAsync(header, cancellation);
        int error = BinaryPrimitives.ReadInt32LittleEndian(header);
        int size = BinaryPrimitives.ReadInt32LittleEndian(header.AsSpan(4));
        Assert.InRange(size, 0, OverlayFiles.MaxRead);
        byte[] data = new byte[size];
        await stream.ReadExactlyAsync(data, cancellation);
        return (error, data);
    }
}
