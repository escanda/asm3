
import asm3.configuration
import asm3.i18n
import asm3.medical

from .base import FTPPublisher
from asm3.sitedefs import PETFINDER_FTP_HOST, PETFINDER_SEND_PHOTOS_BY_FTP

import os
import sys

class PetFinderPublisher(FTPPublisher):
    """
    Handles publishing to PetFinder.com
    """
    def __init__(self, dbo, publishCriteria):
        publishCriteria.forceReupload = True
        publishCriteria.uploadDirectly = True
        publishCriteria.thumbnails = False
        publishCriteria.checkSocket = True
        publishCriteria.scaleImages = 1
        publishCriteria.uploadAllImages = True
        FTPPublisher.__init__(self, dbo, publishCriteria, 
            PETFINDER_FTP_HOST, asm3.configuration.petfinder_user(dbo), 
            asm3.configuration.petfinder_password(dbo))
        self.initLog("petfinder", "PetFinder Publisher")

    def pfDate(self, d):
        """ Returns a CSV entry for a date in YYYY-MM-DD """
        return "\"%s\"" % asm3.i18n.format_date("%Y-%m-%d", d)

    def pfYesNo(self, condition):
        """
        Returns a CSV entry for yes or no based on the condition
        """
        if condition:
            return "\"1\""
        else:
            return "\"\""

    def pfImageUrl(self, urls, index):
        """
        Returns image URL index from urls, returning an empty string if it does not exist.
        """
        try:
            return urls[index]
        except IndexError:
            return ""

    def run(self):

        self.log("PetFinderPublisher starting...")

        if self.isPublisherExecuting(): return
        self.updatePublisherProgress(0)
        self.setLastError("")
        self.setStartPublishing()

        if not self.checkMappedSpecies():
            self.setLastError("Not all species have been mapped.")
            self.cleanup()
            return
        if not self.checkMappedBreeds():
            self.setLastError("Not all breeds have been mapped.")
            self.cleanup()
            return
        shelterid = asm3.configuration.petfinder_user(self.dbo)
        if shelterid == "":
            self.setLastError("No PetFinder.com shelter id has been set.")
            self.cleanup()
            return
        animals = self.getMatchingAnimals()
        if len(animals) == 0:
            self.setLastError("No animals found to publish.")
            self.cleanup()
            return

        if not self.openFTPSocket(): 
            self.setLastError("Failed opening FTP socket.")
            if self.logSearch("530 Login") != -1:
                self.log("Found 530 Login incorrect: disabling PetFinder publisher.")
                asm3.configuration.publishers_enabled_disable(self.dbo, "pf")
            self.cleanup()
            return

        # Make sure necessary folders exist
        self.mkdir("import")
        self.chdir("import")
        self.mkdir("photos")
        self.chdir("photos")

        # Build a list of age bands for petfinder ages. It's
        # a list of integers in days for each band.
        # The defaults are 6 months, 2 years and 9 years. 
        agebands = asm3.configuration.petfinder_age_bands(self.dbo)
        if agebands == "" or len(agebands.split(",")) != 3:
            agebands = "182,730,3285"
        agebands = [ int(i) for i in agebands.split(",") ]

        # It's part of PetFinder's TOS that they will not list animals that
        # are either unaltered, or the shelter will not pre-pay the cost
        # of sterilisation after adoption.
        # At least one of our customers cannot offer this, using a deposit
        # scheme instead which is not covered. They still want to display 
        #  unaltered animals on their own website, so the single "Include unaltered" 
        # publishing option is not enough for them. We need an extra
        # config switch to prevent sending unaltered animals to PetFinder
        # in these cases.
        hide_unaltered = asm3.configuration.petfinder_hide_unaltered(self.dbo)

        csv = []

        anCount = 0
        for an in animals:
            try:
                anCount += 1
                self.log("Processing: %s: %s (%d of %d)" % ( an["SHELTERCODE"], an["ANIMALNAME"], anCount, len(animals)))
                self.updatePublisherProgress(self.getProgress(anCount, len(animals)))

                # If the user cancelled, stop now
                if self.shouldStopPublishing(): 
                    self.log("User cancelled publish. Stopping.")
                    self.resetPublisherProgress()
                    self.cleanup()
                    return

                if PETFINDER_SEND_PHOTOS_BY_FTP:
                    self.uploadImages(an, False, 3)

                if hide_unaltered and an.NEUTERED == 0:
                    self.log("%s is unaltered and petfinder_hide_unaltered == true" % an["ANIMALNAME"])
                    continue

                csv.append( self.processAnimal(an, agebands) )

                # Mark success in the log
                self.logSuccess("Processed: %s: %s (%d of %d)" % ( an["SHELTERCODE"], an["ANIMALNAME"], anCount, len(animals)))

            except Exception as err:
                self.logError("Failed processing animal: %s, %s" % (str(an["SHELTERCODE"]), err), sys.exc_info())

        # Mark published
        self.markAnimalsPublished(animals, first=True)

        # Upload the datafile
        self.chdir("..", "import")
        self.saveFile(os.path.join(self.publishDir, shelterid), "\n".join(csv))
        self.log("Uploading datafile, %s" % shelterid)
        self.upload(shelterid)
        self.log("Uploaded %s" % shelterid)
        self.log("-- FILE DATA -- (csv)")
        self.log("\n".join(csv))
        self.cleanup()

    def processAnimal(self, an, agebands = [ 182, 730, 3285 ]):
        """ Processes an animal and returns a CSV line """
        line = []
        # This specific CSV ordering has been mandated by PetFinder in their import docs of August 2019
        # ID
        line.append("\"%s\"" % an.SHELTERCODE)
        # Internal
        line.append("\"%s\"" % an.SHELTERCODE)
        # AnimalName
        line.append("\"%s\"" % an.ANIMALNAME)
        # PrimaryBreed
        line.append("\"%s\"" % an.PETFINDERBREED)
        # SecondaryBreed
        line.append("\"%s\"" % self.getPublisherBreed(an, 2))
        # Sex, one of M or F
        sexname = "M"
        if an.SEX == 0: sexname = "F"
        line.append("\"%s\"" % sexname)
        # Size, one of S, M, L, XL
        ansize = "M"
        if an.SIZE == 0: ansize = "XL"
        elif an.SIZE == 1: ansize = "L"
        elif an.SIZE == 2: ansize = "M"
        elif an.SIZE == 3: ansize = "S"
        line.append("\"%s\"" % ansize)
        # Age, one of Adult, Baby, Senior and Young
        ageindays = asm3.i18n.date_diff_days(an.DATEOFBIRTH, asm3.i18n.now(self.dbo.timezone))
        agename = "Adult"
        if ageindays < agebands[0]: agename = "Baby"
        elif ageindays < agebands[1]: agename = "Young"
        elif ageindays < agebands[2]: agename = "Adult"
        else: agename = "Senior"
        line.append("\"%s\"" % agename)
        # Description
        line.append("\"%s\"" % self.getDescription(an, crToHE=True, replaceSmart=True))
        # Type (Species)
        line.append("\"%s\"" % an.PETFINDERSPECIES)
        # Status
        line.append("\"Adoptable\"") # TODO: check this
        # Shots
        line.append(self.pfYesNo(asm3.medical.get_vaccinated(self.dbo, int(an.ID))))
        # Altered
        line.append(self.pfYesNo(an.NEUTERED == 1))
        # No Dogs
        line.append(self.pfYesNo(an.ISGOODWITHDOGS == 1))
        # No Cats
        line.append(self.pfYesNo(an.ISGOODWITHCATS == 1))
        # No Kids
        line.append(self.pfYesNo(an.ISGOODWITHCHILDREN == 1))
        # Housetrained
        line.append(self.pfYesNo(an.ISHOUSETRAINED == 0))
        # Declawed
        line.append(self.pfYesNo(an.DECLAWED == 1))
        # Special needs
        if an.CRUELTYCASE == 1:
            line.append("\"1\"")
        elif an.HASSPECIALNEEDS == 1:
            line.append("\"1\"")
        else:
            line.append("\"\"")
        # Mix
        line.append(self.pfYesNo(an.CROSSBREED == 1))
        # photo1-6
        if PETFINDER_SEND_PHOTOS_BY_FTP:
            # Send blanks for the 6 images if we already sent them by FTP
            line.append("\"\"")
            line.append("\"\"")
            line.append("\"\"")
            line.append("\"\"")
            line.append("\"\"")
            line.append("\"\"")
        else:
            urls = self.getPhotoUrls(an.ID)
            line.append("\"%s\"" % self.pfImageUrl(urls, 0)) # photo1
            line.append("\"%s\"" % self.pfImageUrl(urls, 1)) # photo2
            line.append("\"%s\"" % self.pfImageUrl(urls, 2)) # photo3
            line.append("\"%s\"" % self.pfImageUrl(urls, 3)) # photo4
            line.append("\"%s\"" % self.pfImageUrl(urls, 4)) # photo5
            line.append("\"%s\"" % self.pfImageUrl(urls, 5)) # photo6
        # Arrival Date
        line.append(self.pfDate(an.MOSTRECENTENTRYDATE))
        # Birth Date
        line.append(self.pfDate(an.DATEOFBIRTH))
        # primaryColor
        line.append("\"\"") # We don't send this
        # secondaryColor
        line.append("\"\"") # or this
        # tertiaryColor
        line.append("\"\"") # or even this
        # coat_length
        line.append("\"\"") # nope
        # Adoption Fee
        if an.FEE > 0:
            line.append("\"%0.2f\"" % (an.FEE / 100))
        else:
            line.append("\"\"") # send 0 fees as a blank as PF seem to ignore their own display adoption fee flag below
        # Display adoption fee?
        line.append(self.pfYesNo(an.FEE > 0))
        # Adoption fee waived
        line.append("\"\"") # nope
        # Special Needs Notes : Not used
        line.append("\"\"")
        # No Other pets?
        line.append("\"%s\"" % (self.pfYesNo(False)))
        # No Other Note
        line.append("\"\"")
        # Tags
        line.append("\"\"")
        return self.csvLine(line)

