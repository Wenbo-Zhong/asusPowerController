Name:           asus-power-controller
Version:        %{app_version}
Release:        1%{?dist}
Summary:        Desktop power and cooling controls for ASUS laptops
License:        GPL-3.0-or-later
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch
BuildRequires:  desktop-file-utils
Requires:       python3 >= 3.11
Requires:       python3-gobject
Requires:       python3-cairo
Requires:       gtk4
Requires:       libadwaita >= 1.4
Requires:       libayatana-appindicator-gtk3
Requires:       polkit
Requires:       systemd
Requires:       asusctl

%description
GTK desktop controls for ASUS laptop power profiles, fans, battery and lighting.
Hardware operations are provided by asusctl and the asusd service.

%prep
%autosetup

%install
sh packaging/stage.sh "%{buildroot}"
install -Dm644 LICENSE "%{buildroot}%{_licensedir}/%{name}/LICENSE"
desktop-file-validate "%{buildroot}%{_datadir}/applications/%{name}.desktop"

%files
%license %{_licensedir}/%{name}/LICENSE
%{_bindir}/%{name}
%{_prefix}/lib/%{name}
%{_datadir}/applications/%{name}.desktop
