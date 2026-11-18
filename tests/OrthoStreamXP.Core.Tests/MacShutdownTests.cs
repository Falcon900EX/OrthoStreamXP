namespace OrthoStreamXP.Tests;

public sealed class MacShutdownTests
{
    private sealed class Host : IMountHost, IMountHostFactory
    {
        public bool Busy { get; set; } = true;
        public bool Disposed { get; private set; }
        public bool FailStart { get; init; }
        public IMountHost Create(MountContext context) => this;
        public void Mount(int fileSystemThreads)
        {
            if (FailStart) throw new IOException("Synthetic partial mount startup failure");
        }
        public void Unmount()
        {
            if (Busy) throw new IOException("Synthetic busy mount");
        }
        public void Dispose() => Disposed = true;
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void Busy_native_mount_retains_session_for_retry_even_after_partial_startup(bool failStart)
    {
        using var tree = new TempTree();
        Directory.CreateDirectory(tree.PathOf("source/Earth nav data"));
        MountSettings settings = MountSettings.FromValues(TestSettings.Of(
            $"source = {tree.PathOf("source")}", $"cache = {tree.PathOf("cache")}",
            $"mountpoint = {tree.PathOf("mount")}", "mip-streaming = off", "prefetch-loaded-scenery = off"), migrateLegacy: false);
        var host = new Host { FailStart = failStart };
        MountSession session = MountSession.Create(settings, host);
        try
        {
            if (failStart) Assert.Throws<IOException>(session.StartAll);
            else session.StartAll();
            Assert.Throws<IOException>(session.Dispose);
            Assert.False(host.Disposed);
            host.Busy = false;
            session.Dispose();
            Assert.True(host.Disposed);
        }
        finally
        {
            host.Busy = false;
            session.Dispose();
        }
    }
}
