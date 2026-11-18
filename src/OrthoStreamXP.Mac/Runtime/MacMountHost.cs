using System.Diagnostics;

namespace OrthoStreamXP.Mac;

internal sealed class MacMountHostFactory(MacRuntime runtime) : IMountHostFactory
{
    private readonly List<MacMountHost> _hosts = [];

    public IMountHost Create(MountContext context)
    {
        var host = new MacMountHost(context, runtime);
        _hosts.Add(host);
        return host;
    }

    public string? Failure => _hosts.FirstOrDefault(h => h.UnexpectedExit)?.Name;
}

/// <summary>Unmount first, then close the Python process and IPC. Never kill a busy FUSE worker.</summary>
internal sealed class MacMountHost(MountContext context, MacRuntime runtime) : IMountHost
{
    private readonly TaskCompletionSource _ready = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private readonly object _control = new();
    private TaskCompletionSource _busy = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private EngineServer? _engine;
    private Process? _worker;
    private bool _mounted;
    private bool _started;
    private string? _identity;
    private bool _stopping;

    public string Name => context.Plan.Name;
    public bool UnexpectedExit => _mounted && !_stopping && _worker?.HasExited == true;

    public void Mount(int fileSystemThreads)
    {
        if (_worker is not null) throw new InvalidOperationException("Mount already started");
        _engine = new EngineServer(context);
        string identity = _identity = $"OrthoStreamXP-{Guid.NewGuid():N}";
        var arguments = new List<string>
        {
            "--socket", _engine.SocketPath, "--mountpoint", context.Plan.Mountpoint,
            "--identity", identity, "--name", context.Plan.Name,
        };
        if (runtime.DirectIo) arguments.Add("--direct-io");
        _worker = new Process { StartInfo = runtime.PythonProcess("mount_worker.py", [.. arguments]) };
        _worker.OutputDataReceived += (_, e) =>
        {
            if (e.Data == "READY") _ready.TrySetResult();
            else if (e.Data == "BUSY") { lock (_control) _busy.TrySetResult(); }
            else if (!string.IsNullOrWhiteSpace(e.Data)) Log.Info($"macFUSE {Name}: {e.Data}");
        };
        _worker.ErrorDataReceived += (_, e) =>
        {
            if (!string.IsNullOrWhiteSpace(e.Data)) Log.Warning($"macFUSE {Name}: {e.Data}");
        };
        try
        {
            _started = _worker.Start();
            _worker.BeginOutputReadLine();
            _worker.BeginErrorReadLine();
            // The worker is considered owned before READY, so partial startup is cleaned up too.
            _mounted = true;
            Task exit = _worker.WaitForExitAsync();
            Task first = Task.WhenAny(_ready.Task, exit, Task.Delay(TimeSpan.FromSeconds(45))).GetAwaiter().GetResult();
            if (first != _ready.Task || _worker.HasExited)
                throw new IOException($"macFUSE did not initialize {Name}; see the worker messages above");
            Log.Info($"macFUSE ready: {context.Plan.Mountpoint}; native worker concurrency (Windows filesystem-threads={fileSystemThreads} does not set macFUSE threads)");
        }
        catch
        {
            // Let Unmount retain the engine if detachment fails; never close it under a live mount.
            Unmount();
            throw;
        }
    }

    public void Unmount()
    {
        if (_worker is null)
        {
            _engine?.Dispose();
            _engine = null;
            return;
        }
        _stopping = true;
        if (_started && !_worker.HasExited)
        {
            Task busy;
            lock (_control)
            {
                _busy = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
                busy = _busy.Task;
            }
            try
            {
                _worker.StandardInput.WriteLine("stop");
                _worker.StandardInput.Flush();
            }
            catch (IOException) when (_worker.HasExited) { /* Proceed to orphan-detach check. */ }
            Task exit = _worker.WaitForExitAsync();
            Task first = Task.WhenAny(exit, busy, Task.Delay(TimeSpan.FromSeconds(12))).GetAwaiter().GetResult();
            if (first != exit && !_worker.HasExited)
            {
                _stopping = false;
                throw new IOException($"{Name} is still mounted. Close X-Plane, then press Ctrl+C again. Engine and worker are staying alive.");
            }
        }
        // If a worker crashed, try a normal detach. The helper refuses every non-OrthoStreamXP volume.
        // It never walks or deletes the mountpoint and it does not force-unmount implicitly.
        if (_started) runtime.Control("unmount", context.Plan.Mountpoint, "--identity", _identity!);
        _mounted = false;
        _worker.Dispose();
        _worker = null;
        _started = false;
        _engine?.Dispose();
        _engine = null;
    }

    public void Dispose() => Unmount();
}
