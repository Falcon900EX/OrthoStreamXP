using OrthoStreamXP.Scenery;
using OrthoStreamXP.Setup;

namespace OrthoStreamXP;

/// <summary>Native mount lifetime, separated from the shared scenery engine.</summary>
internal interface IMountHost : IDisposable
{
    void Mount(int fileSystemThreads);
    void Unmount();
}

internal interface IMountHostFactory
{
    IMountHost Create(MountContext context);
}

/// <summary>Services used by filesystem read callbacks; owned by MountSession.</summary>
internal sealed record MountContext(PackagePlan Plan, SceneryOverlay Overlay, ReadLog? ReadLog,
    SeasonClock? Seasons, AreaUsage? Usage, ColourMatcher? Colours, SceneryLoads Loads, ushort[]? Exposure);
