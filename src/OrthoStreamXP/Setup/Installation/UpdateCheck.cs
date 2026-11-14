namespace OrthoStreamXP.Setup;

/// <summary>What one look for a new version found.</summary>
internal enum UpdateCheck
{
    /// <summary>A newer version was found and downloaded: it is ready to install.</summary>
    Downloaded,

    /// <summary>A newer version was already downloaded before this look.</summary>
    AlreadyReady,

    /// <summary>This is the newest version.</summary>
    UpToDate,

    /// <summary>Another look is under way.</summary>
    InProgress,

    /// <summary>The look failed (offline, GitHub busy); the reason is in the log.</summary>
    Failed,

    /// <summary>The program was not installed (a build run from the source): it does not look.</summary>
    NotInstalled,
}
