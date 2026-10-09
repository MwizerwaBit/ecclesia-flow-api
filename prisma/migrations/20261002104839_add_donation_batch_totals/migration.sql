-- Hand-stripped of ~230 lines of spurious DROP CONSTRAINT/DROP INDEX, same
-- as every migration after the first — see docs/SECURITY_NOTES.md §6.

-- AlterTable
ALTER TABLE "donation_batches" ADD COLUMN     "donation_count" INTEGER NOT NULL DEFAULT 0,
ADD COLUMN     "total_amount" DECIMAL(12,2) NOT NULL DEFAULT 0;
