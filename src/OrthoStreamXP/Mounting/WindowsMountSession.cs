namespace OrthoStreamXP;

// Keep the existing WPF entry point and all Windows tests source-compatible.
internal sealed partial class MountSession
{
    public static MountSession Start(MountSettings settings) => Start(settings, new WinFspMountHostFactory());
}
