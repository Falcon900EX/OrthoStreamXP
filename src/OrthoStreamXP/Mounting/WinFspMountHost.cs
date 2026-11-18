using Fsp;
using OrthoStreamXP.Scenery;

namespace OrthoStreamXP;

internal sealed class WinFspMountHostFactory : IMountHostFactory
{
    public IMountHost Create(MountContext context) => new WinFspMountHost(context);
}

/// <summary>The original Windows adapter; no FUSE dependencies enter the WPF build.</summary>
internal sealed class WinFspMountHost : IMountHost
{
    private readonly string _mountpoint;
    private readonly FileSystemHost _host;
    private bool _mounted;

    public WinFspMountHost(MountContext context)
    {
        _mountpoint = context.Plan.Mountpoint;
        _host = new FileSystemHost(new SceneryFileSystem(context.Overlay, "OrthoStreamXP", context.ReadLog,
            context.Plan.Name, context.Seasons, context.Usage, context.Colours, context.Loads, context.Exposure));
    }

    public void Mount(int fileSystemThreads)
    {
        int status = _host.MountEx(_mountpoint, (uint)fileSystemThreads, null, false, 0);
        if (status < 0) throw new IOException($"WinFsp could not mount {_mountpoint} (NTSTATUS 0x{status:X8}); is WinFsp installed?");
        _mounted = true;
    }

    public void Unmount()
    {
        if (!_mounted) return;
        _host.Unmount();
        _mounted = false;
    }

    public void Dispose()
    {
        Unmount();
        _host.Dispose();
    }
}
