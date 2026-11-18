using System.Buffers.Binary;
using System.Collections.Concurrent;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using OrthoStreamXP.Scenery;

namespace OrthoStreamXP.Mac;

/// <summary>One private AF_UNIX endpoint per mount. DDS responses are raw bytes.</summary>
internal sealed class EngineServer : IDisposable
{
    private const int MaxRequest = 65536, MaxConnections = 256;
    private static readonly JsonSerializerOptions JsonOptions = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };
    private readonly OverlayFiles _files;
    private readonly Socket _listener = new(AddressFamily.Unix, SocketType.Stream, ProtocolType.Unspecified);
    private readonly CancellationTokenSource _stop = new();
    private readonly ConcurrentDictionary<long, Task> _connections = new();
    private readonly Task _accept;
    private readonly string _directory;
    private long _connectionId;
    private bool _disposed;

    public EngineServer(MountContext context)
    {
        if (OperatingSystem.IsWindows()) throw new PlatformNotSupportedException("The bridge uses POSIX sockets and permissions");
        _files = new OverlayFiles(context);
        // Darwin sockaddr_un.sun_path is short. $TMPDIR can exceed it before we add a filename.
        _directory = Path.Join("/tmp", $"osxp-{Guid.NewGuid():N}");
        SocketPath = Path.Join(_directory, "engine.sock");
        Directory.CreateDirectory(_directory, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
        try
        {
            _listener.Bind(new UnixDomainSocketEndPoint(SocketPath));
            File.SetUnixFileMode(SocketPath, UnixFileMode.UserRead | UnixFileMode.UserWrite);
            _listener.Listen(128);
            _accept = AcceptAsync(_stop.Token);
        }
        catch
        {
            _listener.Dispose();
            _files.Dispose();
            File.Delete(SocketPath);
            Directory.Delete(_directory);
            throw;
        }
    }

    public string SocketPath { get; }

    private async Task AcceptAsync(CancellationToken token)
    {
        while (!token.IsCancellationRequested)
        {
            Socket socket;
            try { socket = await _listener.AcceptAsync(token).ConfigureAwait(false); }
            catch (OperationCanceledException) when (token.IsCancellationRequested) { return; }
            catch (ObjectDisposedException) when (token.IsCancellationRequested) { return; }
            if (_connections.Count >= MaxConnections) { socket.Dispose(); continue; }
            long id = Interlocked.Increment(ref _connectionId);
            Task task = ServeAsync(socket, token);
            _connections[id] = task;
            _ = RemoveCompletedAsync(id, task);
        }
    }

    private async Task RemoveCompletedAsync(long id, Task task)
    {
        try { await task.ConfigureAwait(false); }
        catch (Exception ex) when (ex is IOException or SocketException or OperationCanceledException or ObjectDisposedException)
        {
            if (!_stop.IsCancellationRequested) Log.Detail($"Bridge connection closed: {ex.Message}");
        }
        finally { _connections.TryRemove(id, out _); }
    }

    private async Task ServeAsync(Socket socket, CancellationToken token)
    {
        using var stream = new NetworkStream(socket, ownsSocket: true);
        byte[] header = new byte[4];
        while (!token.IsCancellationRequested)
        {
            int first = await stream.ReadAsync(header.AsMemory(0, 1), token).ConfigureAwait(false);
            if (first == 0) return;
            await stream.ReadExactlyAsync(header.AsMemory(1), token).ConfigureAwait(false);
            int length = BinaryPrimitives.ReadInt32LittleEndian(header);
            if (length is <= 0 or > MaxRequest) return;
            byte[] payload = new byte[length];
            await stream.ReadExactlyAsync(payload, token).ConfigureAwait(false);
            byte[] response;
            int error = 0;
            try
            {
                using JsonDocument doc = JsonDocument.Parse(payload);
                response = Dispatch(doc.RootElement);
            }
            catch (Exception ex) when (ex is not OutOfMemoryException and not StackOverflowException)
            {
                error = ErrorNumber(ex);
                if (error == 5) Log.Error("macFUSE engine request failed", ex);
                // Do not serialize a stack trace or potentially private source pathname.
                response = Encoding.UTF8.GetBytes(error == 2 ? "Not found" : error == 13 ? "Access denied" : $"Filesystem error {error}");
            }
            byte[] replyHeader = new byte[8];
            BinaryPrimitives.WriteInt32LittleEndian(replyHeader, error);
            BinaryPrimitives.WriteInt32LittleEndian(replyHeader.AsSpan(4), response.Length);
            await stream.WriteAsync(replyHeader, token).ConfigureAwait(false);
            await stream.WriteAsync(response, token).ConfigureAwait(false);
        }
    }

    private byte[] Dispatch(JsonElement request)
    {
        if (request.GetProperty("v").GetInt32() != 1) throw new FileSystemError(22, "Protocol version mismatch");
        string op = request.GetProperty("op").GetString() ?? "";
        if (op == "read") return _files.Read(request.GetProperty("fh").GetInt64(), request.GetProperty("offset").GetInt64(), request.GetProperty("size").GetInt32());
        object response = op switch
        {
            "ping" => new { version = 1 },
            "stat" => _files.Stat(RequestPath(request)),
            "fstat" => _files.Stat(request.GetProperty("fh").GetInt64()),
            "open" => new { fh = _files.Open(RequestPath(request)) },
            "opendir" => new { fh = _files.Open(RequestPath(request), directory: true) },
            "readdir" => _files.List(request.GetProperty("fh").GetInt64(), request.GetProperty("offset").GetInt64()),
            "release" => Release(request.GetProperty("fh").GetInt64()),
            _ => throw new FileSystemError(22, "Unknown bridge operation"),
        };
        return JsonSerializer.SerializeToUtf8Bytes(response, JsonOptions);
    }

    private object Release(long id)
    {
        _files.Release(id);
        return new { ok = true };
    }

    private static string RequestPath(JsonElement request) => request.GetProperty("path").GetString()
        ?? throw new FileSystemError(22, "Path is required");

    internal static int ErrorNumber(Exception ex) => ex switch
    {
        FileSystemError e => e.Error,
        FileNotFoundException or DirectoryNotFoundException => 2,
        NtStatusException { Status: NtStatus.ObjectNameNotFound } => 2,
        NtStatusException { Status: NtStatus.AccessDenied } or UnauthorizedAccessException => 13,
        ArgumentException or JsonException or KeyNotFoundException or InvalidOperationException or OverflowException => 22,
        _ => 5,
    };

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        _stop.Cancel();
        _listener.Dispose();
        try
        {
            _accept.GetAwaiter().GetResult();
            Task.WhenAll(_connections.Values).GetAwaiter().GetResult();
        }
        catch (Exception ex) when (ex is OperationCanceledException or IOException or SocketException or ObjectDisposedException)
        {
            Log.Detail($"Bridge shutdown: {ex.Message}");
        }
        _files.Dispose();
        _stop.Dispose();
        File.Delete(SocketPath);
        Directory.Delete(_directory);
    }
}
