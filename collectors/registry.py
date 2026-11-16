"""Church and archive configuration for the first 20 master-sheet entries."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Church:
    number: int
    key: str
    place_id: str
    name: str
    original_archive_url: str
    archive_url: str | None
    platform: str | None
    status: Literal["active", "pending_migration", "excluded"]
    timezone: str | None = None
    page_parameter: str | None = None
    max_pages: int | None = None
    dynamic_player: bool = False


CHURCHES = (
    Church(
        1,
        'christian_valley',
        'ChIJ_wlHNPmthYgRXixEqUWWoEs',
        'Christian Valley Baptist Church',
        'https://www.cvbc-coatopa.org/sermons',
        'https://www.cvbc-coatopa.org/sermons',
        'youtube',
        'active',
    ),
    Church(
        2,
        'mt_pisgah',
        'ChIJ-45RpM_tjYgRGiUarL7ZQVg',
        'Mt. Pisgah Worship Center of Seventh-day Adventists',
        'https://mpwc.adventistchurch.org/online-sermons/item/162',
        'https://mpwc.adventistchurch.org/online-sermons/',
        'adventist',
        'active',
        page_parameter="query-3-page",
        max_pages=15,
        dynamic_player=True,
    ),
    Church(
        3,
        'south_cullman',
        'ChIJ-8UHykXKiYgRPsAWLt6Rwl4',
        'South Cullman church of Christ',
        'https://www.southcullmanchurch.com/sermons/',
        'https://www.southcullmanchurch.com/sermons/',
        'mp3_cards',
        'active',
    ),
    Church(
        4,
        'first_presbyterian_troy',
        'ChIJ05IRruDsjYgRPAq4ZJG2xa0',
        'First Presbyterian Church',
        'https://troyfpc.com/sermons/',
        'https://soundcloud.com/troyfpc/tracks',
        'soundcloud',
        'active',
    ),
    Church(
        5,
        'gateway_church_international',
        'ChIJ14U4BXjWjIgRFbytbCh_bxk',
        'Gateway Church International',
        'https://gatewaychurchinternational.com/services/',
        'http://gatewaychurchinternational.com/services/',
        'wordpress_playlist',
        'active',
    ),
    Church(
        6,
        'redeeming_grace_cullman',
        'ChIJ27MsBrnLiYgRT0pP5hqunRg',
        'Redeeming Grace Church of Cullman, Alabama',
        'https://www.redeeminggracecullman.com/sermons.html',
        'https://www.redeeminggracecullman.com/sermons.html',
        'static_mp3',
        'active',
    ),
    Church(
        7,
        'grace_fellowship',
        'ChIJ41vCmz3GjogRknQx2AkLH0U',
        'Grace Fellowship Church',
        'https://graceclanton.com/resources/sermons',
        'https://graceclanton.sermon.net/main/main/21506093',
        'sermonnet',
        'active',
        timezone="America/Chicago",
    ),
    Church(
        8,
        'faith_covenant',
        'ChIJ54b7TT7vYYgR6trWANR9u0E',
        'Faith Covenant Church of God',
        'https://fcscottsboro.com/sovereign/watch.html',
        'https://www.youtube.com/@faithcovenantchurchofgod1829',
        'youtube',
        'excluded',
    ),
    Church(
        9,
        'christ_church_branch_cove',
        'ChIJ5wlCEOJ5iYgRVqdxPp2uNys',
        'Christ Church - Branch Cove',
        'https://christchurchbranchcove.org/posts/',
        'https://christchurchbranchcove.org/posts/',
        'wordpress_posts',
        'active',
    ),
    Church(
        10,
        'church_of_the_highlands',
        'ChIJ7QAk75BJfYgRpcvxiEmeiDY',
        'Church of the Highlands',
        'https://www.churchofthehighlands.com/media',
        'https://www.churchofthehighlands.com/media',
        'nextjs',
        'active',
    ),
    Church(
        11,
        'first_union_springs_sda',
        'ChIJ7TmmMxevjYgRrw_lFqdPyRk',
        'First Union Springs Seventh-Day Adventist Church',
        'https://firstus.adventistchurch.org/online-sermons?page=1&sort_by=Date&is_ascending=0&search=&tag=&tags=&series=&presenter=',
        'https://firstus.adventistchurch.org/online-sermons/',
        'adventist',
        'active',
    ),
    Church(
        12,
        'grace_community_montgomery',
        'ChIJAQAwAXCBjogR22t-vlUElQU',
        'Grace Community Church – Montgomery, AL',
        'https://www.gracechurchal.com/live-webcast/',
        'https://www.gracechurchal.com/live-webcast/',
        'boxcast',
        'active',
    ),
    Church(
        13,
        'vernon_church_of_christ',
        'ChIJB7TBr5RUhogRRjqrXRAZJWo',
        'Church of Christ',
        'https://www.vernonchurchofchrist.org/sermons/',
        'https://www.vernonchurchofchrist.org/sermons/',
        'wordpress_posts',
        'active',
    ),
    Church(
        14,
        'amazing_grace_alabaster',
        'ChIJBXIZhJwviYgRusC-QKkRvdQ',
        'Amazing Grace Church',
        'https://www.agalabaster.org/watch',
        'https://www.agalabaster.org/recent-media',
        'subsplash',
        'active',
    ),
    Church(
        15,
        'prattville_community',
        'ChIJByN_b0COjogR0q2GXz2TJao',
        'Prattville Community Church',
        'https://www.prattvillecommunitychurch.org/media/sermons/',
        'https://prattvillecommunitychurch.org/media/sermons/',
        'sermon_browser',
        'active',
    ),
    Church(
        16,
        'victory_baptist_moulton',
        'ChIJdQ9cmGmgYogRg3veELkyXfE',
        'Victory Baptist Church',
        'https://vbcmoulton.org/audio_messages',
        'https://vbcmoulton.org/audio_messages',
        'site_media_api',
        'active',
    ),
    Church(
        17,
        'gadsden_city_church',
        'ChIJEWOdKYI9iogRuOULRXCUtr0',
        'Gadsden City Church',
        'https://www.gadsdencitychurch.com/the-blood',
        'https://www.gadsdencitychurch.com/sermons',
        'squarespace',
        'active',
    ),
    Church(
        18,
        'true_worship',
        'ChIJgWPIFQNXkIgRWR8lkXesehk',
        'True Worship Assembly of God',
        'https://www.trueworshipag.org/listen-online/',
        None,
        None,
        'excluded',
    ),
    Church(
        19,
        'covenant_life_ministries',
        'ChIJH8GYBnhLiogRMtCrHEp2GGI',
        'Covenant Life Ministries',
        'https://www.covenantlifeministries.net/media-archives/',
        'https://www.covenantlifeministries.net/media-archives/',
        'sermoncloud',
        'active',
    ),
    Church(
        20,
        'pickens_baptist_association',
        'ChIJh8wkqGBjhogRltoiqjsPzO4',
        'Pickens Baptist Association',
        'https://pickensbaptist.com/sunday-school-online/',
        'https://pickensbaptist.com/sunday-school-online/',
        'wordpress_sermon_manager',
        'active',
    ),
)

CHURCH_BY_KEY = {church.key: church for church in CHURCHES}
